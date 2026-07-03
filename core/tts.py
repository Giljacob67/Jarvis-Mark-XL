"""
Text-to-Speech engines for MARK XL.

EdgeTTS     – free Microsoft TTS (internet required, no API key)
Kokoro      – fully offline neural TTS (~330 MB model)
ElevenLabs  – cloud API (API key required, best quality)
"""
from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable

from core.logger import get_logger

log = get_logger("tts")

import numpy as np
import sounddevice as sd

# USE_TF=0 stops transformers from importing TensorFlow (saves 4-8 s startup).
# Do NOT set USE_TORCH or USE_JAX explicitly — forcing those values breaks
# transformers' lazy-loader on certain versions, causing AutoModel and other
# classes to vanish from the public namespace.  Auto-detection is reliable.
os.environ.setdefault("USE_TF",                 "0")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


# ---------------------------------------------------------------------------
# Audio playback helpers
# ---------------------------------------------------------------------------

def _to_numpy(samples) -> np.ndarray:
    """Convert samples to float32 numpy array.

    Handles both numpy arrays and PyTorch tensors (Kokoro >= 0.9).

    PyTorch built against numpy 1.x raises RuntimeError('Numpy is not available')
    when numpy 2.x is installed.  The .tolist() fallback always works regardless
    of PyTorch / numpy version pairing.
    """
    if hasattr(samples, "detach"):                  # PyTorch tensor
        t = samples.detach().cpu().float()
        try:
            return t.numpy()                        # fast path (compatible versions)
        except RuntimeError:
            # PyTorch/numpy version mismatch — convert via Python list (always safe)
            return np.asarray(t.tolist(), dtype=np.float32)
    return np.asarray(samples, dtype=np.float32)


def _compress_silence(
    arr: np.ndarray,
    sample_rate: int    = 24_000,
    max_silence_ms: int = 500,    # cap punctuation pauses — keeps natural rhythm
    threshold: float    = 0.003,  # RMS below this = silence; lower = less clipping
) -> np.ndarray:
    """
    Shorten Kokoro's very long punctuation pauses (1-2 s → ≤500 ms).
    Conservative settings preserve natural prosody; only trims extreme pauses.

    A ~5 ms micro-fade is applied on both sides of every removed span —
    splicing non-contiguous samples without it produces audible clicks.
    """
    max_samp  = int(max_silence_ms * sample_rate / 1000)
    frame_len = 240                   # ~10 ms at 24 kHz
    fade_n    = 120                   # ~5 ms at 24 kHz
    out: list[np.ndarray] = []
    silent_acc = 0
    skipping   = False

    for i in range(0, len(arr), frame_len):
        chunk = arr[i : i + frame_len]
        if np.sqrt(np.mean(chunk ** 2) + 1e-12) < threshold:
            silent_acc += len(chunk)
            if silent_acc <= max_samp:
                out.append(chunk)
            else:
                if not skipping and out:
                    tail = out[-1].copy()
                    n = min(fade_n, len(tail))
                    tail[-n:] *= np.linspace(1.0, 0.0, n, dtype=np.float32)
                    out[-1] = tail
                skipping = True
        else:
            if skipping:
                chunk = chunk.copy()
                n = min(fade_n, len(chunk))
                chunk[:n] *= np.linspace(0.0, 1.0, n, dtype=np.float32)
                skipping = False
            silent_acc = 0
            out.append(chunk)

    return np.concatenate(out) if out else arr


class AudioOutput:
    """Persistent playback sink — ONE OutputStream kept open across chunks
    and sentences.

    The previous per-chunk sd.play()/sd.wait() opened and closed a PortAudio
    stream for every chunk, producing pops on PulseAudio/PipeWire and gaps
    between chunks of the same response.  Writing to one long-lived stream
    eliminates both.

    play() writes in small slices and polls `stop_evt` between them, so an
    interrupt (barge-in) takes effect within ~90 ms even mid-chunk.
    """

    _SLICE = 2048   # frames per write (~85 ms @ 24 kHz)

    def __init__(self) -> None:
        self._stream: sd.OutputStream | None = None
        self._rate:   int | None = None
        self._lock    = threading.Lock()

    def _ensure_stream(self, rate: int) -> sd.OutputStream:
        with self._lock:
            if self._stream is not None and self._rate != rate:
                try:
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
            if self._stream is None:
                self._stream = sd.OutputStream(
                    samplerate=rate, channels=1, dtype="float32"
                )
                self._rate = rate
                self._stream.start()
            elif not self._stream.active:
                self._stream.start()   # restart after interrupt()'s abort
            return self._stream

    def play(self, samples: np.ndarray, rate: int,
             stop_evt: threading.Event | None = None) -> None:
        """Blocking write of a float32 mono chunk. Interruptible via stop_evt."""
        arr = np.ascontiguousarray(_to_numpy(samples), dtype=np.float32)
        if arr.ndim > 1:
            arr = arr.reshape(-1)
        stream = self._ensure_stream(rate)
        for i in range(0, len(arr), self._SLICE):
            if stop_evt is not None and stop_evt.is_set():
                return
            try:
                stream.write(arr[i : i + self._SLICE])
            except sd.PortAudioError:
                # Stream aborted by interrupt() mid-write — drop the rest.
                return

    def interrupt(self) -> None:
        """Discard pending buffers immediately (abort, not drain)."""
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.abort()
                except Exception:
                    pass

    def close(self) -> None:
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None


# Module-level singleton: primary and fallback engines share one device handle.
_AUDIO_OUT = AudioOutput()


def get_audio_output() -> AudioOutput:
    return _AUDIO_OUT


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------

class EdgeTTSEngine:
    """Microsoft EdgeTTS – free, requires internet.

    synth() yields (float32 mono array, sample_rate) — playback is owned by
    the caller (AudioOutput), so sentence N+1 can synthesise while N plays.
    """

    def __init__(self, voice: str = "pt-BR-AntonioNeural"):
        self.voice = voice
        self._loop: asyncio.AbstractEventLoop | None = None

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        # Reuse one event loop per engine (a fresh loop per sentence added
        # avoidable setup cost on every utterance).
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def synth(self, text: str, stop_evt: threading.Event | None = None):
        audio_bytes = self._get_loop().run_until_complete(self._synth(text))
        if stop_evt is not None and stop_evt.is_set():
            return
        if not audio_bytes:
            return
        import miniaudio
        decoded = miniaudio.decode(
            audio_bytes,
            output_format=miniaudio.SampleFormat.FLOAT32,
            nchannels=1,
        )
        yield np.array(decoded.samples, dtype=np.float32), decoded.sample_rate

    async def _synth(self, text: str) -> bytes:
        import edge_tts
        comm = edge_tts.Communicate(text, self.voice)
        buf  = bytearray()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)


# ---------------------------------------------------------------------------
# Kokoro import helper — auto-upgrades on version-mismatch errors
# ---------------------------------------------------------------------------

# Errors that indicate the installed kokoro uses old transformers classes
# (AlbertModel, AutoModel) that are no longer exported at the top level.
_KOKORO_COMPAT_ERRORS = ("AlbertModel", "AutoModel", "cannot import name")


def _import_kokoro_pipeline():
    """Import KPipeline, auto-upgrading kokoro if a version mismatch is found.

    Old kokoro (<0.9) imports AlbertModel / AutoModel from transformers.
    Newer transformers versions no longer export these at the top level,
    causing an ImportError.  kokoro>=0.9 removed these dependencies.

    When the error is detected we:
      1. Upgrade kokoro to >=0.9 via pip (silent, background)
      2. Flush stale kokoro entries from sys.modules
      3. Re-import — this time it should succeed
    """
    import sys

    def _try_import():
        from kokoro import KPipeline  # noqa: PLC0415
        return KPipeline

    try:
        return _try_import()
    except Exception as first_err:
        err_msg = str(first_err)
        if not any(marker in err_msg for marker in _KOKORO_COMPAT_ERRORS):
            # Unrelated error (kokoro not installed, etc.)
            raise RuntimeError(
                f"Kokoro import failed: {first_err}\n"
                "Run: pip install kokoro>=0.9 soundfile"
            ) from first_err

        # ── Version mismatch: upgrade kokoro silently and retry ──────────
        log.info("Kokoro/transformers version mismatch detected — upgrading kokoro")
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "kokoro>=0.9",
             "--upgrade", "--quiet", "--disable-pip-version-check"],
            capture_output=True,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace").strip()
            raise RuntimeError(
                f"Kokoro auto-upgrade failed: {stderr[:200]}\n"
                "Run manually: pip install kokoro>=0.9 soundfile"
            ) from first_err

        # Flush any stale kokoro submodules from the import cache
        stale = [k for k in sys.modules if k == "kokoro" or k.startswith("kokoro.")]
        for key in stale:
            del sys.modules[key]

        log.info("Kokoro upgraded — retrying import")
        try:
            return _try_import()
        except Exception as retry_err:
            raise RuntimeError(
                f"Kokoro still broken after upgrade: {retry_err}\n"
                "Run manually: pip install --upgrade kokoro transformers"
            ) from retry_err


# Kokoro voice prefix → KPipeline lang_code mapping
_KOKORO_LANG_CODES = {
    "a": "a",   # American English  (af_*, am_*)
    "b": "b",   # British English   (bf_*, bm_*)
    "j": "j",   # Japanese          (jf_*, jm_*)
    "z": "z",   # Mandarin Chinese  (zf_*, zm_*)
    "s": "s",   # Spanish           (sf_*, sm_*)
    "f": "f",   # French            (ff_*, fm_*)
    "h": "h",   # Hindi             (hf_*, hm_*)
    "i": "i",   # Italian           (if_*, im_*)
    "p": "p",   # Brazilian Portuguese
    "r": "r",   # Russian           (rf_*, rm_*)
    "e": "e",   # German            (ef_*, em_*)
}


class KokoroTTSEngine:
    """Fully offline Kokoro neural TTS.

    Model (~330 MB) is downloaded from HuggingFace on first use,
    then cached locally — subsequent starts load from disk.

    Warmup strategy: _init() runs synchronously in the background
    _do_tts() thread (not the UI thread).  After the pipeline loads,
    a dummy inference compiles the PyTorch JIT graph immediately so
    the first real speak() call has zero compilation overhead.
    """

    def __init__(self, voice: str = "af_heart", speed: float = 1.0):
        self.voice     = voice
        self.speed     = speed
        self._pipeline = None
        self._lock     = threading.Lock()
        self._init()   # blocking, but called from background thread

    @property
    def _lang_code(self) -> str:
        prefix = self.voice[0].lower() if self.voice else "a"
        return _KOKORO_LANG_CODES.get(prefix, "a")

    def _init(self) -> None:
        if self._pipeline is not None:
            return

        lang = self._lang_code

        # Prefer GPU — Kokoro on CUDA is ~10x faster than CPU.
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
            if device == "cpu":
                import os as _os
                n_threads = max(1, min(4, (_os.cpu_count() or 4) // 2))
                try:
                    torch.set_num_threads(n_threads)
                    torch.set_num_interop_threads(2)
                except RuntimeError:
                    pass
        except Exception:
            device = "cpu"

        log.info("Kokoro — loading (lang='%s', device='%s')", lang, device)

        KPipeline = _import_kokoro_pipeline()

        def _create_pipeline():
            try:
                return KPipeline(lang_code=lang, device=device)
            except TypeError:
                return KPipeline(lang_code=lang)   # older build — no device param

        try:
            self._pipeline = _create_pipeline()
        except Exception as _first_err:
            # Offline flag set but model not cached yet → download once
            _e = str(_first_err).lower()
            if any(k in _e for k in ("offline", "not found", "cache", "localentry", "does not exist")):
                log.info("Kokoro model not cached — downloading (internet required for first run)")
                os.environ.pop("HF_HUB_OFFLINE",      None)
                os.environ.pop("TRANSFORMERS_OFFLINE", None)
                os.environ.pop("HF_DATASETS_OFFLINE",  None)
                self._pipeline = _create_pipeline()
            else:
                raise

        log.info("Kokoro compiling (first-time only)")
        # Warmup: compiles PyTorch JIT graph so first real speak() call is instant.
        try:
            for _ in self._pipeline("hello", voice=self.voice, speed=self.speed):
                pass
            log.info("Kokoro ready")
        except Exception as e:
            log.warning("Kokoro warmup warning: %s", e)

    def synth(self, text: str, stop_evt: threading.Event | None = None):
        """Yield (float32 chunk, 24000) as Kokoro generates them.

        Playback pipelining lives in the caller (main.py's synth/play worker
        pair) — chunk N+1 synthesises while chunk N plays.
        """
        with self._lock:
            if self._pipeline is None:
                self._init()

        for _, _, audio in self._pipeline(text, voice=self.voice, speed=self.speed):
            if stop_evt is not None and stop_evt.is_set():
                break
            if audio is not None:
                arr = _compress_silence(_to_numpy(audio))
                if arr.size > 0:
                    yield arr, 24_000


class ElevenLabsTTSEngine:
    """ElevenLabs cloud TTS – API key required.

    model_id options (set via tts_model in config):
      eleven_turbo_v2_5   — fastest, great for assistants (recommended)
      eleven_flash_v2_5   — even lower latency, slightly less expressive
      eleven_multilingual_v2 — highest quality, slower
    No extra toggle on the ElevenLabs website — just pass model_id in the API body.
    """

    def __init__(
        self,
        api_key: str,
        voice_id: str = "GIuLCSVfgJaUuh7hYOY8",
        model_id: str = "eleven_turbo_v2_5",
        stability: float = 0.5,
        similarity_boost: float = 0.75,
        speed: float = 1.0,
    ):
        self.api_key  = api_key
        self.voice_id = voice_id
        self.model_id = model_id or "eleven_turbo_v2_5"
        self.stability = stability
        self.similarity_boost = similarity_boost
        self.speed = speed

    def synth(self, text: str, stop_evt: threading.Event | None = None):
        """Yield (float32 chunk, 24000) as PCM arrives from the API."""
        import requests
        headers = {
            "xi-api-key":   self.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "text":     text,
            "model_id": self.model_id,
            "voice_settings": {
                "stability":        self.stability,
                "similarity_boost": self.similarity_boost,
                "speed":            self.speed,
            },
        }
        # PCM + optimize_streaming_latency=4 → first audio in ~300ms (no full MP3 wait).
        params = {
            "output_format":              "pcm_24000",
            "optimize_streaming_latency": "4",
        }
        resp = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}/stream",
            params=params,
            json=payload,
            headers=headers,
            timeout=60,
            stream=True,
        )
        resp.raise_for_status()
        leftover = b""
        for chunk in resp.iter_content(chunk_size=8192):
            if stop_evt is not None and stop_evt.is_set():
                resp.close()
                return
            if not chunk:
                continue
            data = leftover + chunk
            trim = len(data) - (len(data) % 2)   # int16 needs even byte count
            if trim <= 0:
                leftover = data
                continue
            pcm = np.frombuffer(data[:trim], dtype=np.int16).astype(np.float32) / 32768.0
            leftover = data[trim:]
            yield pcm, 24_000


# ---------------------------------------------------------------------------
# Thread-safe player wrapper
# ---------------------------------------------------------------------------

class TTSPlayer:
    """
    Wraps any *Engine (which only synthesises). Playback goes through the
    shared persistent AudioOutput.

    synth(text) — chunk generator for pipelined playback (main.py's
                  synth-worker / play-worker pair).
    speak(text) — blocking synth+play convenience (used by the fallback
                  path and scripts); same audio sink.
    """

    def __init__(self, engine):
        self._engine   = engine
        self._playing  = False
        self._lock     = threading.Lock()
        self._stop_evt = threading.Event()

    @property
    def is_playing(self) -> bool:
        return self._playing

    def synth(self, text: str):
        """Yield (float32 chunk, sample_rate). Aborts when stop() is called."""
        self._stop_evt.clear()
        yield from self._engine.synth(text, stop_evt=self._stop_evt)

    @property
    def stop_event(self) -> threading.Event:
        return self._stop_evt

    def speak(
        self,
        text:     str,
        on_start: Callable | None = None,
        on_done:  Callable | None = None,
    ) -> None:
        """Synthesise and play text. BLOCKING – call from a dedicated thread.

        Raises on engine failure so the caller can log to the UI and try a
        fallback engine — a swallowed error here means silent dead air.
        """
        try:
            self._stop_evt.clear()
            with self._lock:
                self._playing = True
            if on_start:
                on_start()
            out = get_audio_output()
            for samples, rate in self._engine.synth(text, stop_evt=self._stop_evt):
                if self._stop_evt.is_set():
                    break
                out.play(samples, rate, stop_evt=self._stop_evt)
        except Exception as e:
            log.error("TTS Error: %s", e)
            raise
        finally:
            with self._lock:
                self._playing = False
            if on_done:
                on_done()

    def stop(self) -> None:
        """Interrupt synthesis + playback. Safe to call from any thread."""
        self._stop_evt.set()            # synth generators poll this per chunk
        get_audio_output().interrupt()  # discard buffered audio immediately
        with self._lock:
            self._playing = False


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_tts_player(config: dict) -> TTSPlayer:
    engine_name = config.get("tts_engine", "edgetts").lower()
    if engine_name == "kokoro":
        voice  = config.get("tts_voice", "af_heart")
        speed  = float(config.get("tts_speed", 1.0))
        engine = KokoroTTSEngine(voice=voice, speed=speed)
    elif engine_name == "elevenlabs":
        api_key          = config.get("elevenlabs_api_key", "")
        voice_id         = config.get("tts_voice", "GIuLCSVfgJaUuh7hYOY8")
        model_id         = config.get("tts_model", "eleven_turbo_v2_5")
        stability        = float(config.get("tts_stability", 0.5))
        similarity_boost = float(config.get("tts_similarity_boost", 0.75))
        speed            = float(config.get("tts_speed", 1.0))
        engine = ElevenLabsTTSEngine(
            api_key=api_key,
            voice_id=voice_id,
            model_id=model_id,
            stability=stability,
            similarity_boost=similarity_boost,
            speed=speed,
        )
    else:   # edgetts (default)
        voice  = config.get("tts_voice", "pt-BR-AntonioNeural")
        engine = EdgeTTSEngine(voice=voice)
    return TTSPlayer(engine)
