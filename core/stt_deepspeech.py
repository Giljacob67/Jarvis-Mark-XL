"""
MARK XL — Deep Speech STT engine.

DeepSpeech is lighter than Whisper (50M vs 1.5B params) and works well
for real-time transcription. Uses Mozilla's DeepSpeech model.

Installation: pip install deepspeech
Model download: https://github.com/mozilla/DeepSpeech/releases
"""
from __future__ import annotations

import numpy as np

from core.logger import get_logger

log = get_logger("stt.deepspeech")


class DeepSpeechSTT:
    """Offline transcription using Mozilla DeepSpeech."""

    def __init__(self, model_path: str | None = None, language: str = "en"):
        try:
            from deepspeech import Model
        except ImportError:
            raise RuntimeError(
                "DeepSpeech not installed. Run: pip install deepspeech\n"
                "Download model from: https://github.com/mozilla/DeepSpeech/releases"
            )

        if not model_path:
            # Try default paths
            default_paths = [
                "models/deepspeech-0.9.3-models.pbmm",
                "deepspeech-0.9.3-models.pbmm",
            ]
            for p in default_paths:
                if Path(p).exists():
                    model_path = p
                    break

        if not model_path:
            raise RuntimeError(
                "DeepSpeech model not found. Download from:\n"
                "https://github.com/mozilla/DeepSpeech/releases\n"
                "Place .pbmm file in models/ directory."
            )

        log.info("Loading DeepSpeech model from %s", model_path)
        self._model = Model(model_path)

        # Try to load scorer for better accuracy
        scorer_path = model_path.replace(".pbmm", ".scorer")
        if Path(scorer_path).exists():
            try:
                self._model.enableExternalScorer(scorer_path)
                log.info("Scorer loaded: %s", scorer_path)
            except Exception as e:
                log.warning("Could not load scorer: %s", e)

        self._language = language
        log.info("DeepSpeech ready (model: %s)", model_path)

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe a float32 mono 16 kHz numpy array. Returns transcript string."""
        try:
            # DeepSpeech expects int16 samples
            audio_int16 = (audio * 32767).astype(np.int16)
            text = self._model.stt(audio_int16)
            return text.strip()
        except Exception as e:
            log.error("Transcription error: %s", e)
            raise
