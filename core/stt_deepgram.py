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
        endpointing_ms: int = 300,
        utterance_end_ms: int = 1000,
    ):
        self._api_key = (api_key or _load_api_key()).strip()
        if not self._api_key:
            raise RuntimeError("Deepgram API key missing.")
        self._model = model or "nova-2"
        lang = (language or "auto").strip().lower()
        self._language = _LANG_MAP.get(lang, lang if lang != "auto" else "pt-BR")
        self._sample_rate = sample_rate
        self._on_final = on_final
        self._on_interim = on_interim
        self._endpointing_ms = max(100, min(int(endpointing_ms), 1000))
        self._utterance_end_ms = max(1000, int(utterance_end_ms))
        self._ws = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._connect_err: str | None = None
        self._connected = False
        self._closed = False
        self._utterance_start: float | None = None
        log.info("Deepgram live ready (model=%s, lang=%s)", self._model, self._language)

    def _build_url(self) -> str:
        # utterance_end_ms must be >= 1000 (Deepgram returns 400 otherwise).
        q = urlencode({
            "model":            self._model,
            "language":         self._language,
            "encoding":         "linear16",
            "sample_rate":      self._sample_rate,
            "channels":         1,
            "interim_results":  "true",
            "endpointing":      self._endpointing_ms,
            "utterance_end_ms": self._utterance_end_ms,
            "punctuate":        "true",
        })
        return f"wss://api.deepgram.com/v1/listen?{q}"

    def start(self, timeout: float = 15) -> None:
        import websocket

        self._ready.clear()
        self._connect_err = None

        def _on_open(ws) -> None:
            self._connected = True
            self._ready.set()
            log.debug("Deepgram live socket open")

        def _on_message(ws, raw: str) -> None:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return
            if data.get("type") != "Results":
                return
            alt = data.get("channel", {}).get("alternatives", [{}])[0]
            text = (alt.get("transcript") or "").strip()
            if not text:
                return
            if not data.get("is_final"):
                if self._utterance_start is None:
                    self._utterance_start = time.time()
                if self._on_interim:
                    self._on_interim(text)
                return
            if data.get("speech_final"):
                stt_ms = 0.0
                if self._utterance_start is not None:
                    stt_ms = (time.time() - self._utterance_start) * 1000
                self._utterance_start = None
                self._on_final(text, stt_ms)

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
        self._thread = threading.Thread(
            target=lambda: self._ws.run_forever(ping_interval=20, ping_timeout=10),
            daemon=True,
            name="deepgram-live",
        )
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            detail = self._connect_err or "no response from server"
            raise RuntimeError(
                f"Deepgram live WebSocket did not connect: {detail}"
            )

    def feed(self, pcm_int16: bytes) -> None:
        """Send raw int16 mono PCM to the live stream."""
        if self._closed or not self._ws or not self._ws.sock:
            return
        try:
            import websocket
            self._ws.send(pcm_int16, opcode=websocket.ABNF.OPCODE_BINARY)
        except Exception as e:
            log.debug("Deepgram feed skipped: %s", e)

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