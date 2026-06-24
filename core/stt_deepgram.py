"""
Deepgram STT — cloud transcription via Nova-2.

Low-latency alternative to local Whisper. Requires deepgram_api_key in config.
"""
from __future__ import annotations

import io
import json
import wave

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
    """Cloud transcription using Deepgram listen API."""

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
        log.info("Deepgram ready (model=%s, lang=%s)", self._model, self._language)

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16_000) -> str:
        """Transcribe float32 mono PCM. Returns transcript string."""
        wav = _to_wav_bytes(audio, sample_rate)
        params = {
            "model": self._model,
            "language": self._language,
            "smart_format": "true",
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
                timeout=30,
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