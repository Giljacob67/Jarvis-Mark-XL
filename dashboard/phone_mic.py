"""
MARK XL — Phone Microphone Processor.

Buffers PCM16 audio frames from the phone WebSocket, converts to float32,
applies VAD (energy + spectral centroid), and transcribes via Whisper.

The processor runs asynchronously — audio frames are queued and processed
in a background thread to avoid blocking the WebSocket event loop.
"""
from __future__ import annotations

import asyncio
import queue
import threading
import time
from collections.abc import Callable

import numpy as np

from core.logger import get_logger

log = get_logger("phone_mic")


class PhoneMicProcessor:
    """
    Processes PCM16 audio from phone WebSocket → VAD → Whisper transcription.

    Usage:
        processor = PhoneMicProcessor(stt_engine, on_transcription callback)
        processor.start()

        # In WebSocket handler:
        processor.feed_audio(pcm16_bytes)

        # When done:
        processor.stop()
    """

    SAMPLE_RATE = 16000
    CHANNELS = 1

    def __init__(
        self,
        stt_engine=None,
        on_transcription: Callable[[str], None] | None = None,
        on_partial: Callable[[str], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        broadcast_fn: Callable | None = None,
        silence_sec: float = 1.5,
        speech_thresh: float = 0.008,
        silence_thresh: float = 0.004,
        min_speech_sec: float = 0.3,
        max_speech_sec: float = 30.0,
        centroid_thresh: float = 1500.0,
    ):
        self._stt = stt_engine
        self._on_transcription = on_transcription
        self._on_partial = on_partial
        self._on_status = on_status
        self._broadcast_fn = broadcast_fn

        # VAD parameters
        self._sr = self.SAMPLE_RATE
        self._sil_n = int(silence_sec * self.SAMPLE_RATE)
        self._speech_thresh = speech_thresh
        self._sil_thresh = silence_thresh
        self._min_n = int(min_speech_sec * self.SAMPLE_RATE)
        self._max_n = int(max_speech_sec * self.SAMPLE_RATE)
        self._centroid_thresh = centroid_thresh

        # State
        self._buf: list[np.ndarray] = []
        self._in_speech = False
        self._sil_cnt = 0
        self._audio_queue: queue.Queue = queue.Queue(maxsize=500)
        self._running = False
        self._thread: threading.Thread | None = None
        self._frame_count = 0
        self._utterance_count = 0

    def start(self) -> None:
        """Start the processing thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._process_loop, daemon=True)
        self._thread.start()
        if self._on_status:
            self._on_status("Phone mic processor started")
        log.info("Phone mic processor started")

    def stop(self) -> None:
        """Stop the processing thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        # Clear queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break
        self._buf.clear()
        self._in_speech = False
        self._sil_cnt = 0
        if self._on_status:
            self._on_status("Phone mic processor stopped")
        log.info("Phone mic processor stopped (processed %d frames, %d utterances)",
                 self._frame_count, self._utterance_count)

    def feed_audio(self, pcm16_bytes: bytes) -> None:
        """
        Feed raw PCM16 LE audio bytes from the phone WebSocket.
        Frames are queued for async processing.
        """
        if not self._running:
            return
        try:
            self._audio_queue.put_nowait(pcm16_bytes)
            self._frame_count += 1
        except queue.Full:
            pass  # drop frame rather than block

    def is_active(self) -> bool:
        return self._running

    def get_stats(self) -> dict:
        return {
            "running": self._running,
            "frames_processed": self._frame_count,
            "utterances_transcribed": self._utterance_count,
            "queue_size": self._audio_queue.qsize(),
            "in_speech": self._in_speech,
            "buffer_samples": sum(len(c) for c in self._buf),
        }

    def _spectral_centroid(self, chunk: np.ndarray) -> float:
        fft = np.abs(np.fft.rfft(chunk))
        freqs = np.fft.rfftfreq(len(chunk), d=1.0 / self._sr)
        total = np.sum(fft)
        if total < 1e-10:
            return 0.0
        return float(np.sum(freqs * fft) / total)

    def _vad_process(self, chunk: np.ndarray) -> np.ndarray | None:
        """
        VAD: energy + spectral centroid.
        Returns complete utterance when speech ends, otherwise None.
        """
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        centroid = self._spectral_centroid(chunk)
        total_n = sum(len(c) for c in self._buf)

        is_voice = rms > self._speech_thresh and centroid > self._centroid_thresh
        is_noise = rms > self._speech_thresh and centroid <= self._centroid_thresh

        if is_voice:
            self._in_speech = True
            self._sil_cnt = 0
            self._buf.append(chunk.copy())
        elif is_noise and not self._in_speech:
            pass
        elif self._in_speech:
            self._buf.append(chunk.copy())
            if rms < self._sil_thresh:
                self._sil_cnt += len(chunk)

            if self._sil_cnt >= self._sil_n or total_n >= self._max_n:
                audio = np.concatenate(self._buf)
                self._buf = []
                self._in_speech = False
                self._sil_cnt = 0
                if len(audio) >= self._min_n:
                    return audio

        return None

    def _transcribe(self, audio: np.ndarray) -> str:
        """Transcribe float32 audio via the STT engine."""
        if self._stt is None:
            return ""
        try:
            return self._stt.transcribe(audio)
        except Exception as e:
            log.error("Transcription error: %s", e)
            return ""

    def _process_loop(self) -> None:
        """Main processing loop — reads from queue, applies VAD, transcribes."""
        log.info("Phone mic processing loop started")

        while self._running:
            try:
                pcm16_bytes = self._audio_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            # Convert PCM16 bytes → float32 numpy array
            try:
                pcm16 = np.frombuffer(pcm16_bytes, dtype=np.int16)
                audio_f32 = pcm16.astype(np.float32) / 32768.0
            except Exception:
                continue

            # Process in chunks of 1024 samples (64ms at 16kHz)
            chunk_size = 1024
            for i in range(0, len(audio_f32), chunk_size):
                chunk = audio_f32[i:i + chunk_size]
                if len(chunk) < chunk_size:
                    # Pad last chunk
                    chunk = np.pad(chunk, (0, chunk_size - len(chunk)))

                utterance = self._vad_process(chunk)
                if utterance is not None:
                    # Transcribe complete utterance
                    self._utterance_count += 1
                    log.info("Utterance #%d: %d samples (%.1fs)",
                             self._utterance_count, len(utterance),
                             len(utterance) / self._sr)

                    text = self._transcribe(utterance)
                    if text and text.strip():
                        log.info("Phone transcription: '%s'", text.strip())
                        if self._on_transcription:
                            self._on_transcription(text.strip())
                        # Broadcast transcription event to dashboard
                        if self._broadcast_fn:
                            try:
                                import asyncio
                                asyncio.ensure_future(self._broadcast_fn({
                                    "type": "phone_transcription",
                                    "text": text.strip(),
                                    "utterance": self._utterance_count,
                                }))
                            except Exception:
                                pass
                    else:
                        log.debug("Empty transcription for utterance #%d",
                                  self._utterance_count)

        log.info("Phone mic processing loop ended")
