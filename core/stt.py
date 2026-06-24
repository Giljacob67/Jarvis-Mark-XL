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

    def __init__(
        self,
        model_name: str = "base",
        language: str | None = None,
        beam_size: int = 5,
        best_of: int = 5,
        use_webrtc_vad: bool = True,
    ):
        import os

        from faster_whisper import WhisperModel
        log.info("Loading Whisper '%s' (beam=%d, best_of=%d, webrtc_vad=%s)",
                 model_name, beam_size, best_of, use_webrtc_vad)
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
        self._beam_size = beam_size
        self._best_of = best_of
        self._use_webrtc_vad = use_webrtc_vad
        log.info("Whisper '%s' ready (%s)", model_name, device)

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe a float32 mono 16 kHz numpy array. Returns transcript string."""
        try:
            # WebRTC VAD parameters for better voice detection
            vad_params = {
                "min_silence_duration_ms": 300,
                "speech_pad_ms": 200,  # Add padding around speech
            }
            
            if self._use_webrtc_vad:
                # Use more aggressive VAD settings
                vad_params.update({
                    "threshold": 0.5,  # WebRTC VAD threshold (0-1)
                    "min_speech_duration_ms": 250,
                    "max_speech_duration_s": 30,
                })
            
            segments, _ = self._model.transcribe(
                audio,
                language=self._language,
                beam_size=self._beam_size,
                best_of=self._best_of,
                condition_on_previous_text=False,
                vad_filter=True,
                vad_parameters=vad_params,
                temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],  # Temperature fallback
                no_speech_threshold=0.6,
                compression_ratio_threshold=2.4,
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
