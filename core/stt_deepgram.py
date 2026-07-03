"""
Deepgram STT — cloud transcription via Nova-2.

Batch (HTTP) and live (WebSocket) modes. Live streaming cuts voice latency
by transcribing while the user speaks instead of waiting for VAD silence + upload.
"""
from __future__ import annotations

import io
import json
import threading
import time
import wave
from collections.abc import Callable
from urllib.parse import urlencode

import numpy as np
import requests

from core.logger import get_logger
from core.paths import API_CONFIG_PATH

log = get_logger("stt.deepgram")

_LANG_MAP = {
    "auto": "pt-BR",
    "pt": "pt-BR",
    "en": "en-US",
    "es": "es",
    "fr": "fr",
    "de": "de",
    "it": "it",
    "tr": "tr",
}


def _load_api_key() -> str:
    try:
        cfg = json.loads(API_CONFIG_PATH.read_text(encoding="utf-8"))
        return (cfg.get("deepgram_api_key") or "").strip()
    except Exception:
        return ""


def _to_wav_bytes(audio: np.ndarray, sample_rate: int = 16_000) -> bytes:
    """Encode float32 mono PCM as 16-bit WAV in memory."""
    pcm = np.clip(audio, -1.0, 1.0)
    pcm16 = (pcm * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue()


class DeepgramSTT:
    """Batch transcription using Deepgram listen API (fallback)."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "nova-2",
        language: str | None = None,
    ):
        self._api_key = (api_key or _load_api_key()).strip()
        if not self._api_key:
            raise RuntimeError(
                "Deepgram API key missing. Set deepgram_api_key in config/api_keys.json"
            )
        self._model = model or "nova-2"
        lang = (language or "auto").strip().lower()
        self._language = _LANG_MAP.get(lang, lang if lang != "auto" else "pt-BR")
        log.info("Deepgram batch ready (model=%s, lang=%s)", self._model, self._language)

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16_000) -> str:
        """Transcribe float32 mono PCM. Returns transcript string."""
        wav = _to_wav_bytes(audio, sample_rate)
        params = {
            "model": self._model,
            "language": self._language,
            "punctuate": "true",
        }
        headers = {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": "audio/wav",
        }
        try:
            resp = requests.post(
                "https://api.deepgram.com/v1/listen",
                params=params,
                headers=headers,
                data=wav,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            alt = (
                data.get("results", {})
                .get("channels", [{}])[0]
                .get("alternatives", [{}])[0]
            )
            return (alt.get("transcript") or "").strip()
        except requests.exceptions.HTTPError as e:
            body = e.response.text[:200] if e.response is not None else ""
            log.error("Deepgram HTTP %s: %s", getattr(e.response, "status_code", "?"), body)
            raise RuntimeError(f"Deepgram transcription failed: {body}") from e
        except Exception as e:
            log.error("Deepgram error: %s", e)
            raise


class DeepgramLiveSTT:
    """
    Real-time Deepgram transcription over WebSocket.

    Feed int16 PCM chunks via feed(). Final utterances are delivered to on_final.
    """

    def __init__(
        self,
        on_final: Callable[[str, float], None],
        api_key: str | None = None,
        model: str = "nova-2",
        language: str | None = None,
        on_interim: Callable[[str], None] | None = None,
        sample_rate: int = 16_000,
        keywords: list[str] | None = None,
    ):
        self._api_key = (api_key or _load_api_key()).strip()
        if not self._api_key:
            raise RuntimeError("Deepgram API key missing.")
        self._model = model or "nova-2"
        lang = (language or "auto").strip().lower()
        self._language = _LANG_MAP.get(lang, lang if lang != "auto" else "pt-BR")
        self._sample_rate = sample_rate
        # Always boost the wake word; extra terms come from config
        # ("deepgram_keywords": ["nome:3", ...] — 'word:intensifier').
        self._keywords = ["jarvis:5"] + [k for k in (keywords or []) if k]
        self._on_final = on_final
        self._on_interim = on_interim
        self._ws = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._connect_err: str | None = None
        self._connected = False
        self._closed = False
        self._utterance_start: float | None = None
        # Segmentos is_final acumulados até o speech_final/UtteranceEnd.
        # Deepgram fecha "chunks" no meio da fala (is_final=True sem
        # speech_final) — descartá-los perde o começo de frases longas.
        self._segments: list[str] = []
        log.info("Deepgram live ready (model=%s, lang=%s)", self._model, self._language)

    # ── montagem de frases (testável isoladamente) ───────────────────────
    def _flush_segments(self) -> None:
        """Entrega a frase acumulada (se houver) e zera o estado."""
        full = " ".join(self._segments).strip()
        self._segments = []
        stt_ms = 0.0
        if self._utterance_start is not None:
            stt_ms = (time.time() - self._utterance_start) * 1000
        self._utterance_start = None
        if full:
            self._on_final(full, stt_ms)

    def _handle_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        mtype = data.get("type")
        if mtype == "UtteranceEnd":
            # Gap de fala detectado por timing de palavras — entrega o que
            # está pendente mesmo que nenhum speech_final tenha vindo
            # (acontece com ruído de fundo mantendo o canal "ativo").
            self._flush_segments()
            return
        if mtype != "Results":
            return
        alt  = data.get("channel", {}).get("alternatives", [{}])[0]
        text = (alt.get("transcript") or "").strip()
        if not data.get("is_final"):
            if text:
                if self._utterance_start is None:
                    self._utterance_start = time.time()
                if self._on_interim:
                    # mostra a frase inteira em andamento, não só o chunk atual
                    self._on_interim(" ".join([*self._segments, text]).strip())
            return
        # is_final: fecha um SEGMENTO (não necessariamente a fala inteira)
        if text:
            self._segments.append(text)
        # speech_final/from_finalize: a fala terminou — entrega tudo.
        # (speech_final pode vir com transcript vazio; por isso o flush
        # NÃO pode ficar atrás do 'if text'.)
        if data.get("speech_final") or data.get("from_finalize"):
            self._flush_segments()

    def _build_url(self) -> str:
        # utterance_end_ms must be >= 1000 (Deepgram returns 400 otherwise).
        params: list[tuple[str, str]] = [
            ("model",            self._model),
            ("language",         self._language),
            ("encoding",         "linear16"),
            ("sample_rate",      str(self._sample_rate)),
            ("channels",         "1"),
            ("interim_results",  "true"),
            ("endpointing",      "300"),
            ("utterance_end_ms", "1000"),
            ("punctuate",        "true"),
            ("smart_format",     "true"),
        ]
        # Keyword boosting — biases recognition toward expected words (the
        # wake word above all: fixes 'Jarbes'/'Jarves' mishears).
        # nova-3 replaced `keywords` with `keyterm`; send the right one.
        key_param = "keyterm" if self._model.startswith("nova-3") else "keywords"
        for kw in self._keywords:
            params.append((key_param, kw))
        return f"wss://api.deepgram.com/v1/listen?{urlencode(params)}"

    def start(self, timeout: float = 15) -> None:
        import websocket

        self._ready.clear()
        self._connect_err = None
        self._last_audio = time.time()

        self._thread = threading.Thread(
            target=self._run, daemon=True, name="deepgram-live",
        )
        self._thread.start()
        # KeepAlive: o Deepgram derruba a conexão após ~10s SEM áudio.
        # Enquanto o gate do mic está fechado (Jarvis falando uma resposta
        # longa, mudo), mandamos KeepAlive para segurar o socket aberto.
        self._ka_thread = threading.Thread(
            target=self._keepalive_loop, daemon=True, name="deepgram-ka",
        )
        self._ka_thread.start()
        if not self._ready.wait(timeout=timeout):
            detail = self._connect_err or "no response from server"
            raise RuntimeError(
                f"Deepgram live WebSocket did not connect: {detail}"
            )

    def _build_ws(self):
        import websocket

        def _on_open(ws) -> None:
            self._connected = True
            self._ready.set()
            log.debug("Deepgram live socket open")

        def _on_message(ws, raw: str) -> None:
            self._handle_message(raw)

        def _on_error(ws, err) -> None:
            self._connect_err = str(err)
            log.warning("Deepgram live error: %s", err)

        def _on_close(ws, code, msg) -> None:
            log.debug("Deepgram live closed (%s): %s", code, msg)
            if not self._connected and msg:
                self._connect_err = str(msg)
            self._ready.clear()

        self._ws = websocket.WebSocketApp(
            self._build_url(),
            header={"Authorization": f"Token {self._api_key}"},
            on_open=_on_open,
            on_message=_on_message,
            on_error=_on_error,
            on_close=_on_close,
        )

    def _run(self) -> None:
        """Connection loop with auto-reconnect — a dropped socket used to
        kill voice recognition for the rest of the session."""
        backoff = 1
        while not self._closed:
            self._build_ws()
            try:
                self._ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as e:
                log.warning("Deepgram run_forever: %s", e)
            if self._closed:
                return
            was_connected = self._connected
            self._connected = False
            self.reset_utterance()
            backoff = 1 if was_connected else min(backoff * 2, 15)
            log.warning("Deepgram live desconectou — reconectando em %ds", backoff)
            time.sleep(backoff)

    def _keepalive_loop(self) -> None:
        while not self._closed:
            time.sleep(3)
            try:
                if self._connected and time.time() - self._last_audio > 3:
                    self._ws.send(json.dumps({"type": "KeepAlive"}))
            except Exception:
                pass   # socket caindo — o _run reconecta

    def feed(self, pcm_int16: bytes) -> None:
        """Send raw int16 mono PCM to the live stream."""
        if self._closed or not self._ws or not self._ws.sock:
            return
        try:
            import websocket
            self._last_audio = time.time()
            self._ws.send(pcm_int16, opcode=websocket.ABNF.OPCODE_BINARY)
        except Exception as e:
            log.debug("Deepgram feed skipped: %s", e)

    def finalize(self) -> None:
        """Flush Deepgram's buffered audio — call whenever the mic gate CLOSES
        (TTS speaking, mute, cooldown).

        Deepgram only finalizes when it hears silence IN THE AUDIO IT RECEIVES.
        If we just stop sending packets mid-utterance, that utterance never
        finalizes; when feeding resumes, its stale words get stitched into the
        NEXT utterance — finals arrive late (10s+), out of order and garbled.
        The flushed final lands while the gate is still closed, so the echo
        guard in _handle_voice_transcript discards it.
        """
        if self._closed or not self._ws or not self._ws.sock:
            return
        try:
            self._ws.send(json.dumps({"type": "Finalize"}))
        except Exception as e:
            log.debug("Deepgram finalize skipped: %s", e)
        self._utterance_start = None

    def reset_utterance(self) -> None:
        """Drop client-side utterance state when the mic gate re-opens."""
        self._utterance_start = None
        self._segments = []

    def feed_float(self, audio: np.ndarray) -> None:
        """Convert float32 [-1,1] mono chunk to int16 and send."""
        pcm16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        self.feed(pcm16)

    def close(self) -> None:
        self._closed = True
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass