"""
Speaker Diarization for JARVIS continuous mode.

Uses pyannote.audio to identify different speakers in the audio stream,
allowing JARVIS to distinguish between the user and other voices.
"""
from __future__ import annotations

import logging
import os
import threading
from collections import deque
from pathlib import Path
from typing import Optional

import numpy as np

from core.logger import get_logger
from core.paths import BASE_DIR

log = get_logger("diarization")

# Suppress pyannote/huggingface verbose logging
logging.getLogger("pyannote").setLevel(logging.WARNING)
logging.getLogger("speechbrain").setLevel(logging.WARNING)

_DIA_PIPELINE = None
_DIA_LOCK = threading.Lock()
_DIARIZATION_ENABLED = False

# Rolling buffer for speaker tracking (last 30 seconds of embeddings)
_SPEAKER_BUFFER: deque = deque(maxlen=300)  # 300 chunks @ 100ms = 30s
_USER_EMBEDDING: Optional[np.ndarray] = None
_USER_EMBEDDING_PATH = BASE_DIR / "memory" / "user_embedding.npy"


def _load_user_embedding() -> Optional[np.ndarray]:
    """Load enrolled user voice embedding."""
    global _USER_EMBEDDING
    if _USER_EMBEDDING is not None:
        return _USER_EMBEDDING
    if _USER_EMBEDDING_PATH.exists():
        try:
            _USER_EMBEDDING = np.load(_USER_EMBEDDING_PATH)
            log.info("Loaded user voice embedding")
        except Exception as e:
            log.warning("Failed to load user embedding: %s", e)
    return _USER_EMBEDDING


def save_user_embedding(embedding: np.ndarray) -> bool:
    """Save enrolled user voice embedding."""
    global _USER_EMBEDDING
    try:
        _USER_EMBEDDING_PATH.parent.mkdir(parents=True, exist_ok=True)
        np.save(_USER_EMBEDDING_PATH, embedding)
        _USER_EMBEDDING = embedding
        log.info("Saved user voice embedding")
        return True
    except Exception as e:
        log.error("Failed to save user embedding: %s", e)
        return False


def _get_pipeline():
    """Lazy-load pyannote diarization pipeline."""
    global _DIA_PIPELINE, _DIARIZATION_ENABLED
    
    if _DIA_PIPELINE is not None:
        return _DIA_PIPELINE
    
    with _DIA_LOCK:
        if _DIA_PIPELINE is not None:
            return _DIA_PIPELINE
        
        try:
            from pyannote.audio import Pipeline
            
            # Use HF_TOKEN from environment or config
            hf_token = os.environ.get("HF_TOKEN", "")
            if not hf_token:
                # Try to load from config
                import json
                config_path = BASE_DIR / "config" / "api_keys.json"
                if config_path.exists():
                    try:
                        config = json.loads(config_path.read_text())
                        hf_token = config.get("hf_token", "")
                    except Exception:
                        pass
            
            if not hf_token:
                log.warning("No HF_TOKEN found. Diarization requires pyannote access token from huggingface.co")
                _DIARIZATION_ENABLED = True  # Mark as attempted
                return None
            
            log.info("Loading pyannote speaker diarization pipeline...")
            _DIA_PIPELINE = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                use_auth_token=hf_token,
            )
            _DIARIZATION_ENABLED = True
            log.info("Diarization pipeline loaded")
            
        except ImportError:
            log.warning("pyannote.audio not installed. Run: pip install pyannote.audio")
            _DIARIZATION_ENABLED = True
        except Exception as e:
            log.error("Failed to load diarization pipeline: %s", e)
            _DIARIZATION_ENABLED = True
    
    return _DIA_PIPELINE


def is_available() -> bool:
    """Check if diarization is available."""
    return _get_pipeline() is not None


def diarize_audio(audio: np.ndarray, sample_rate: int = 16000) -> list[dict]:
    """
    Run speaker diarization on audio.
    
    Returns list of segments: [{"start": float, "end": float, "speaker": str}, ...]
    """
    pipeline = _get_pipeline()
    if not pipeline:
        return []
    
    try:
        # Convert to torch tensor for pyannote
        import torch
        waveform = torch.from_numpy(audio).float().unsqueeze(0)  # (1, samples)
        
        # Run diarization
        diarization = pipeline({"waveform": waveform, "sample_rate": sample_rate})
        
        segments = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            segments.append({
                "start": turn.start,
                "end": turn.end,
                "speaker": speaker,
            })
        
        return segments
        
    except Exception as e:
        log.error("Diarization failed: %s", e)
        return []


def extract_speaker_embedding(audio: np.ndarray, sample_rate: int = 16000) -> Optional[np.ndarray]:
    """Extract speaker embedding from audio segment using speechbrain ECAPA-TDNN."""
    try:
        from speechbrain.inference.speaker import EncoderClassifier
        import torch
        
        # Load classifier (cached)
        if not hasattr(extract_speaker_embedding, "_classifier"):
            extract_speaker_embedding._classifier = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir=str(BASE_DIR / "models" / "ecapa-voxceleb"),
                run_opts={"device": "cuda" if torch.cuda.is_available() else "cpu"},
            )
        
        classifier = extract_speaker_embedding._classifier
        waveform = torch.from_numpy(audio).float().unsqueeze(0)
        embedding = classifier.encode_batch(waveform).squeeze().cpu().numpy()
        return embedding
        
    except ImportError:
        log.warning("speechbrain not installed. Run: pip install speechbrain")
        return None
    except Exception as e:
        log.error("Embedding extraction failed: %s", e)
        return None


def is_user_speaking(audio: np.ndarray, sample_rate: int = 16000, threshold: float = 0.75) -> bool:
    """
    Check if the audio contains the enrolled user's voice.
    
    Returns True if user embedding matches with cosine similarity > threshold.
    """
    user_emb = _load_user_embedding()
    if user_emb is None:
        return True  # No enrollment - accept all
    
    current_emb = extract_speaker_embedding(audio, sample_rate)
    if current_emb is None:
        return True  # Can't verify - accept
    
    # Cosine similarity
    similarity = np.dot(user_emb, current_emb) / (
        np.linalg.norm(user_emb) * np.linalg.norm(current_emb) + 1e-10
    )
    
    log.debug("Voice match similarity: %.3f", similarity)
    return similarity >= threshold


class DiarizationProcessor:
    """
    Real-time diarization processor for continuous mode.
    
    Maintains speaker state across audio chunks and identifies
    when the enrolled user is speaking.
    """
    
    def __init__(self, sample_rate: int = 16000, chunk_duration: float = 0.1):
        self.sample_rate = sample_rate
        self.chunk_size = int(sample_rate * chunk_duration)
        self._buffer = np.array([], dtype=np.float32)
        self._current_speaker = "unknown"
        self._speaker_history: deque = deque(maxlen=50)
    
    def process_chunk(self, chunk: np.ndarray) -> tuple[str, bool]:
        """
        Process audio chunk and return (speaker_label, is_user).
        
        Accumulates chunks and runs diarization periodically.
        """
        self._buffer = np.concatenate([self._buffer, chunk.flatten()])
        
        # Process when we have enough audio (2+ seconds)
        if len(self._buffer) >= self.sample_rate * 2:
            segments = diarize_audio(self._buffer, self.sample_rate)
            
            if segments:
                # Get the speaker for the most recent segment
                latest = segments[-1]
                self._current_speaker = latest["speaker"]
                self._speaker_history.append(self._current_speaker)
                
                # Check if it's the enrolled user
                is_user = False
                if latest["end"] - latest["start"] > 0.5:  # Minimum segment duration
                    start_sample = int(latest["start"] * self.sample_rate)
                    end_sample = int(latest["end"] * self.sample_rate)
                    segment_audio = self._buffer[start_sample:end_sample]
                    is_user = is_user_speaking(segment_audio, self.sample_rate)
            
            # Keep only last 10 seconds in buffer
            keep_samples = self.sample_rate * 10
            if len(self._buffer) > keep_samples:
                self._buffer = self._buffer[-keep_samples:]
        
        return self._current_speaker, is_user if 'is_user' in locals() else True


def enroll_user_from_audio(audio: np.ndarray, sample_rate: int = 16000) -> bool:
    """Enroll user voice from audio sample (min 5 seconds recommended)."""
    if len(audio) < sample_rate * 3:
        log.warning("Audio too short for enrollment (need 3+ seconds)")
        return False
    
    embedding = extract_speaker_embedding(audio, sample_rate)
    if embedding is not None:
        return save_user_embedding(embedding)
    return False