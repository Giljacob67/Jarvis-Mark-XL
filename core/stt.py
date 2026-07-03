"""
Speech-to-Text engines for MARK XL.

Whisper  – offline transcription via faster-whisper (VAD-buffered)
Vosk     – offline streaming transcription (lighter)
"""
from __future__ import annotations

import json

import numpy as np

from core.logger import get_logger

log = get_logger("stt")


class WhisperSTT:
    """Offline transcription using faster-whisper."""

    def __init__(self, model_name: str = "base", language: str | None = None):
        import os

        from faster_whisper import WhisperModel
        log.info("Loading Whisper '%s'", model_name)
        try:
            import torch
            device  = "cuda" if torch.cuda.is_available() else "cpu"
            compute = "float16" if device == "cuda" else "int8"
        except Exception:
            device, compute = "cpu", "int8"

        try:
            self._model = WhisperModel(model_name, device=device, compute_type=compute)
        except Exception as _first_err:
            _e = str(_first_err).lower()
            if any(k in _e for k in ("offline", "not found", "cache", "localentry", "does not exist")):
                log.info("'%s' not cached — downloading (internet required for first run)", model_name)
                os.environ.pop("HF_HUB_OFFLINE",      None)
                os.environ.pop("TRANSFORMERS_OFFLINE", None)
                os.environ.pop("HF_DATASETS_OFFLINE",  None)
                self._model = WhisperModel(model_name, device=device, compute_type=compute)
            else:
                raise

        self._language = None if (not language or language.strip().lower() == "auto") else language.strip().lower()
        log.info("Whisper '%s' ready (%s)", model_name, device)

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe a float32 mono 16 kHz numpy array. Returns transcript string."""
        try:
            segments, _ = self._model.transcribe(
                audio,
                language=self._language,
                beam_size=1,
                best_of=1,
                condition_on_previous_text=False,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
            )
            return " ".join(s.text for s in segments).strip()
        except Exception as e:
            log.error("Transcription error: %s", e)
            raise


class VoskSTT:
    """Streaming transcription using Vosk."""

    def __init__(self, model_path: str | None = None, language: str = "en-us"):
        from vosk import KaldiRecognizer, Model
        log.info("Loading Vosk model")
        if model_path:
            model = Model(model_path)
        else:
            lang  = language.strip().lower() if language and language.strip().lower() != "auto" else "en-us"
            model = Model(lang=lang)
        self._rec = KaldiRecognizer(model, 16000)
        log.info("Vosk ready")

    def process_chunk(self, audio_bytes: bytes) -> tuple[str, bool]:
        """Feed raw int16 LE PCM bytes. Returns (text, is_final)."""
        if self._rec.AcceptWaveform(audio_bytes):
            result = json.loads(self._rec.Result())
            return result.get("text", ""), True
        partial = json.loads(self._rec.PartialResult())
        return partial.get("partial", ""), False

    def reset(self) -> None:
        """Drop the partial utterance — call when the mic gate closes, so
        pre-gate words aren't stitched onto the next utterance."""
        try:
            self._rec.Reset()
        except Exception:
            pass
