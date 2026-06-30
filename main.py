"""
MARK XL — Local LLM Edition
STT (Whisper / Vosk)  +  Ollama LLM  +  TTS (EdgeTTS / Kokoro / ElevenLabs)
All Gemini / Google-AI dependencies removed.
"""
# ── Silence verbose logs + block heavy unused backends ─────────────────────
import os as _os
_os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL",  "3")   # TensorFlow C++ noise
_os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")   # oneDNN banner
_os.environ.setdefault("GRPC_VERBOSITY",         "ERROR")
# USE_TF=0 prevents transformers from importing TensorFlow (saves 4-8 s).
# We intentionally do NOT set USE_TORCH or USE_JAX — forcing those values
# breaks transformers' lazy-loader on some versions (AutoModel disappears
# from the namespace).  Let transformers auto-detect the available backends.
_os.environ.setdefault("USE_TF",                 "0")
_os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
# Offline mode — use cached models, no HuggingFace network calls on startup.
# On first run the model isn't cached yet; tts.py / stt.py detect this and
# temporarily clear these flags to allow the one-time download, then they
# stay in effect for every subsequent launch (fully offline).
_os.environ.setdefault("HF_HUB_OFFLINE",      "1")
_os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
_os.environ.setdefault("HF_DATASETS_OFFLINE",  "1")
import warnings as _warnings
_warnings.filterwarnings("ignore", category=UserWarning)
_warnings.filterwarnings("ignore", category=DeprecationWarning)
_warnings.filterwarnings("ignore", category=FutureWarning)
# ───────────────────────────────────────────────────────────────────────────

# ── Venv + bootstrap ───────────────────────────────────────────────────────
# Debian/Ubuntu Python 3.11+ is PEP 668 "externally managed" — system pip is
# blocked.  We always run inside a project-local .venv (created on first launch).
import importlib.util as _ilu
import subprocess      as _sp
import sys             as _sys

_PROJECT_ROOT = _os.path.dirname(_os.path.abspath(__file__))
_VENV_DIR     = _os.path.join(_PROJECT_ROOT, ".venv")
_VENV_PYTHON  = _os.path.join(_VENV_DIR, "bin", "python")

_BASE_PKGS = [
    ("PyQt6",       "PyQt6"),
    ("psutil",      "psutil"),
    ("numpy",       "numpy"),
    ("sounddevice", "sounddevice"),
    ("PIL",         "pillow"),
    ("requests",    "requests"),
]


def _in_venv() -> bool:
    return _sys.prefix != _sys.base_prefix


def _ensure_venv() -> None:
    """Create .venv if missing, then re-exec with its Python interpreter."""
    if _in_venv():
        return
    if not _os.path.isfile(_VENV_PYTHON):
        print("\n[MARK XL] Creating virtual environment (.venv)…")
        _sp.run([_sys.executable, "-m", "venv", _VENV_DIR], check=True)
        print("[MARK XL] .venv ready.\n")
    print("[MARK XL] Switching to .venv…")
    _os.execv(_VENV_PYTHON, [_VENV_PYTHON] + _sys.argv)


def _bootstrap() -> None:
    need = [pkg for mod, pkg in _BASE_PKGS if _ilu.find_spec(mod) is None]
    if not need:
        return
    print(f"\n[MARK XL] First-run setup — installing: {', '.join(need)}")
    print("[MARK XL] This happens only once.\n")
    _sp.run(
        [_sys.executable, "-m", "pip", "install", *need,
         "--quiet", "--disable-pip-version-check"],
        check=True,
    )
    print("\n[MARK XL] Base packages ready — restarting…\n")
    _os.execv(_sys.executable, [_sys.executable] + _sys.argv)


_ensure_venv()
_bootstrap()
# ───────────────────────────────────────────────────────────────────────────

import json
import queue
import re
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd

# Tell Qt where its platform plugins live before the first Qt import in ui.py.
# Without this, QLibraryInfo returns an empty path when launched from a
# launchd .app bundle because Python's app-bundle context doesn't find the
# venv-installed PyQt6 plugins automatically.
if _sys.platform == "darwin":
    _here = _os.path.dirname(_os.path.abspath(__file__))
    _py_ver = f"python{_sys.version_info.major}.{_sys.version_info.minor}"
    _qt_plugins = _os.path.join(
        _here, ".venv", "lib", _py_ver, "site-packages", "PyQt6", "Qt6", "plugins"
    )
    if _os.path.isdir(_qt_plugins):
        _os.environ.setdefault("QT_PLUGIN_PATH", _qt_plugins)
        _os.environ.setdefault(
            "QT_QPA_PLATFORM_PLUGIN_PATH", _os.path.join(_qt_plugins, "platforms")
        )

from ui import JarvisUI
from memory.memory_manager import load_memory, update_memory, format_memory_for_prompt
from core.llm_client import call_llm, call_llm_stream, get_llm_settings
from core.logger import get_logger

log = get_logger("jarvis")

from actions.file_processor    import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import screen_process
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.game_updater      import game_updater


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

from core.paths import BASE_DIR, API_CONFIG_PATH, PROMPT_PATH

SAMPLE_RATE_IN = 16_000
BLOCK_SIZE     = 1_024
CHANNELS       = 1
VOICE_SESSION_ECHO_GUARD_SEC = 0.6
DEFAULT_ECHO_GUARD_SEC = 1.2

# ---------------------------------------------------------------------------
# Tool declarations — imported from canonical source
# ---------------------------------------------------------------------------

from core.tools import TOOL_DECLARATIONS, OLLAMA_TOOLS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    try:
        with open(API_CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, Tony Stark's AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )


# Minimal prompt for casual chat — no tool rules, ~30 tokens vs ~400+.
_CHAT_SYSTEM_PROMPT = (
    "You are JARVIS, a personal AI assistant. "
    "Reply in the user's language. Max 2 short sentences. Be direct and friendly."
)


# ---------------------------------------------------------------------------
# Voice Activity Detection (used for Whisper listen loop)
# ---------------------------------------------------------------------------

class _VADBuffer:
    """Enhanced VAD: energy + spectral centroid for robust speech detection."""

    def __init__(
        self,
        sample_rate:    int   = 16_000,
        silence_sec:    float = 0.40,   # silence after last word → send to STT
        speech_thresh:  float = 0.008,  # RMS above this = speech
        silence_thresh: float = 0.004,  # RMS below this = silence (hysteresis)
        min_speech_sec: float = 0.3,
        max_speech_sec: float = 30.0,
        centroid_thresh: float = 1500.0,  # spectral centroid above this = voice (not noise)
    ):
        self._sr             = sample_rate
        self._sil_n          = int(silence_sec * sample_rate)
        self._speech_thresh  = speech_thresh
        self._sil_thresh     = silence_thresh
        self._min_n          = int(min_speech_sec * sample_rate)
        self._max_n          = int(max_speech_sec * sample_rate)
        self._centroid_thresh = centroid_thresh
        self._buf:           list[np.ndarray] = []
        self._in_spch        = False
        self._sil_cnt        = 0

    def _spectral_centroid(self, chunk: np.ndarray) -> float:
        """Compute spectral centroid — higher values indicate voice vs noise."""
        fft = np.abs(np.fft.rfft(chunk))
        freqs = np.fft.rfftfreq(len(chunk), d=1.0 / self._sr)
        total = np.sum(fft)
        if total < 1e-10:
            return 0.0
        return float(np.sum(freqs * fft) / total)

    def process(self, chunk: np.ndarray) -> np.ndarray | None:
        """
        Feed one audio chunk (float32 mono).
        Returns complete utterance when speech ends, otherwise None.

        Uses energy + spectral centroid:
          - speech starts when RMS > speech_thresh AND centroid > 1500 Hz
          - speech ends only when RMS < silence_thresh
        The centroid check rejects background noise (fans, hums) that have
        low spectral content.
        """
        rms      = float(np.sqrt(np.mean(chunk ** 2)))
        centroid = self._spectral_centroid(chunk)
        total_n  = sum(len(c) for c in self._buf)

        # Voice detection: energy above threshold AND spectral centroid indicates voice
        is_voice = rms > self._speech_thresh and centroid > self._centroid_thresh
        is_noise = rms > self._speech_thresh and centroid <= self._centroid_thresh

        if is_voice:
            self._in_spch = True
            self._sil_cnt = 0
            self._buf.append(chunk.copy())
        elif is_noise and not self._in_spch:
            pass  # reject noise when not already in speech
        elif self._in_spch:
            self._buf.append(chunk.copy())
            if rms < self._sil_thresh:
                self._sil_cnt += len(chunk)

            if self._sil_cnt >= self._sil_n or total_n >= self._max_n:
                audio         = np.concatenate(self._buf)
                self._buf     = []
                self._in_spch = False
                self._sil_cnt = 0
                if len(audio) >= self._min_n:
                    return audio
        return None


# ---------------------------------------------------------------------------
# JarvisLocal
# ---------------------------------------------------------------------------

class JarvisLocal:
    """
    Main assistant class.
    Replaces JarvisLive (Gemini Live API) with:
      STT (Whisper/Vosk) → Ollama LLM (tool calling) → TTS (Edge/Kokoro/ElevenLabs)
    """

    # Wake word configuration
    WAKE_WORD = "jarvis"
    WAKE_WORD_VARIANTS = [
        "jarvis", "járvis", "jarviz", "jarvys", "jarviss", "jarves", "jarvi",
        # Common STT misrecognitions (EN + PT-BR):
        "travis", "trevis", "tarvis", "jarvist", "jarvas",
        "james", "jarmes", "jarmis", "germes", "djervis", "jervis",
        "jarviso", "charvis", "yarvis",
        "olá jarvis", "oi jarvis",
    ]
    # PT-BR prefixes — exact start match only (no fuzzy; avoids "estar"≈"escuta")
    WAKE_PREFIX_VARIANTS = ["escuta", "na escuta"]
    _WAKE_FUZZY_MIN_LEN = 5
    _WAKE_FUZZY_THRESHOLD = 0.82

    # Only attach tool schema when the user likely wants an action (Groq chokes on 32 tools for "oi").
    _ACTION_RE = re.compile(
        r"(?i)\b("
        r"abr[ae]|open|launch|lanc|inici|execut|"
        r"pesquis|busca|search|googl|"
        r"envi|mand|messag|whatsapp|telegram|email|"
        r"timer|alarm|lembret|remind|"
        r"tempo|weather|clima|"
        r"youtube|spotify|music|"
        r"tela|screen|camera|webcam|"
        r"arquiv|file|past|"
        r"calend|agend|"
        r"instal|download|"
        r"calcul|traduz|nota|clipboard|copi"
        r")\w*"
    )

    def __init__(self, ui: JarvisUI):
        self.ui               = ui
        self._config          = _load_config()
        self._stt             = None
        self._tts             = None
        self._tts_ready       = threading.Event()   # set when TTS engine is loaded
        self._speaking        = False
        self._speaking_lock   = threading.Lock()
        self._text_queue:     queue.Queue = queue.Queue()
        self._tts_queue:      queue.Queue = queue.Queue()
        self._conversation:   list[dict]  = []
        self._turn_lock       = threading.Lock()
        self._listen_blocked_until = 0.0   # ignore mic briefly after TTS (echo guard)
        self._voice_session_until  = 0.0   # follow-up voice without repeating "Jarvis"
        self._voice_turn_seq       = 0
        self._voice_metrics: dict[int, dict] = {}
        self._voice_metrics_lock   = threading.Lock()
        self._active_voice_turn_id: int | None = None
        self._active_voice_turn_lock = threading.Lock()
        self._last_voice_transcript_norm = ""
        self._last_voice_transcript_at = 0.0
        self._stt_executor        = ThreadPoolExecutor(max_workers=2, thread_name_prefix="stt")
        self._dashboard           = None
        self._dashboard_loop      = None
        self._dashboard_cmd_sync: queue.Queue = queue.Queue()
        self._phone_pcm_sync:    queue.Queue = queue.Queue()
        self._phone_active        = False
        self._proactive_mode      = bool(self._config.get("proactive_mode", False))
        self._proactive_interval_sec = int(self._config.get("proactive_interval_sec", 900))
        self._last_user_activity  = time.time()
        self._last_proactive_at   = 0.0

        # Continuous mode: listen without wake word
        self._continuous_mode = self._config.get("continuous_mode", False)

        # Persistent conversation storage
        from memory.conversation_db import init_db, create_conversation
        init_db()
        self._conv_id = create_conversation()

        self.ui.on_text_command   = self._on_text_command
        self.ui.on_remote_clicked = self._make_remote_key

    def _mic_blocked(self) -> bool:
        """True while Jarvis is speaking, phone mic active, or post-TTS cooldown."""
        if self._phone_active:
            return True
        with self._speaking_lock:
            if self._speaking:
                return True
        return time.time() < self._listen_blocked_until

    def _voice_session_secs(self) -> float:
        return float(self._config.get("voice_session_sec", 45))

    @staticmethod
    def _normalize_transcript_for_dedupe(text: str) -> str:
        text = (text or "").strip().lower()
        text = re.sub(r"[^\w\sÀ-ÿ]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _mark_user_activity(self) -> None:
        self._last_user_activity = time.time()

    def _proactive_worker(self) -> None:
        """Optional proactive nudges when the assistant is idle for long periods."""
        if self._proactive_interval_sec < 60:
            self._proactive_interval_sec = 60
        prompts = [
            "Sir, if you want I can check your calendar and summarize the next commitments.",
            "Sir, I can run a quick system health check whenever you want.",
            "Sir, I am ready to draft messages, emails, or reminders if needed.",
        ]
        idx = 0
        while True:
            time.sleep(5)
            if not self._proactive_mode:
                continue
            if self.ui.muted or self._mic_blocked():
                continue
            now = time.time()
            if now - self._last_user_activity < self._proactive_interval_sec:
                continue
            if now - self._last_proactive_at < self._proactive_interval_sec:
                continue
            with self._speaking_lock:
                if self._speaking:
                    continue
            if self._in_voice_session():
                continue

            self.speak(prompts[idx % len(prompts)])
            self._last_proactive_at = now
            self._last_user_activity = now
            idx += 1

    def _extend_voice_session(self) -> None:
        """Keep listening for follow-up questions without saying Jarvis again."""
        self._voice_session_until = time.time() + self._voice_session_secs()

    def _in_voice_session(self) -> bool:
        return time.time() < self._voice_session_until

    def _begin_voice_turn(self, stt_ms: float) -> int:
        """Create a new voice-turn metric context."""
        with self._voice_metrics_lock:
            self._voice_turn_seq += 1
            turn_id = self._voice_turn_seq
            self._voice_metrics[turn_id] = {
                "t0": time.time(),
                "stt_ms": int(stt_ms) if stt_ms > 0 else None,
                "first_token_ms": None,
                "first_sentence_ms": None,
                "first_audio_ms": None,
            }
            # Keep memory bounded.
            if len(self._voice_metrics) > 20:
                oldest = sorted(self._voice_metrics.keys())[:-20]
                for old_id in oldest:
                    self._voice_metrics.pop(old_id, None)
        return turn_id

    def _is_turn_active(self, turn_id: int | None) -> bool:
        if turn_id is None:
            return True
        with self._active_voice_turn_lock:
            return self._active_voice_turn_id == turn_id

    def _activate_voice_turn(self, stt_ms: float) -> int:
        """Activate a new voice turn and cancel stale audio from previous turns."""
        turn_id = self._begin_voice_turn(stt_ms)
        with self._active_voice_turn_lock:
            self._active_voice_turn_id = turn_id

        # Cancel currently playing/queued audio from older turns.
        try:
            if self._tts:
                self._tts.stop()
        except Exception:
            pass
        while True:
            try:
                self._tts_queue.get_nowait()
                self._tts_queue.task_done()
            except queue.Empty:
                break

        return turn_id

    def _set_voice_metric(self, turn_id: int | None, key: str, value_ms: int) -> None:
        if turn_id is None:
            return
        with self._voice_metrics_lock:
            entry = self._voice_metrics.get(turn_id)
            if not entry:
                return
            if entry.get(key) is None:
                entry[key] = value_ms

    def _voice_turn_elapsed_ms(self, turn_id: int | None) -> int | None:
        if turn_id is None:
            return None
        with self._voice_metrics_lock:
            entry = self._voice_metrics.get(turn_id)
            if not entry:
                return None
            t0 = entry.get("t0")
            if not isinstance(t0, (int, float)):
                return None
            return int((time.time() - t0) * 1000)

    def _on_tts_start_for_turn(self, turn_id: int | None) -> None:
        """Called right before TTS synthesis starts for a voice turn."""
        if turn_id is None:
            return
        if not self._is_turn_active(turn_id):
            return
        elapsed = self._voice_turn_elapsed_ms(turn_id)
        if elapsed is None:
            return
        self._set_voice_metric(turn_id, "first_audio_ms", elapsed)
        with self._voice_metrics_lock:
            entry = self._voice_metrics.get(turn_id, {})
            stt_ms = entry.get("stt_ms")
            token_ms = entry.get("first_token_ms")
            sent_ms = entry.get("first_sentence_ms")
            audio_ms = entry.get("first_audio_ms")
        self.ui.write_log(
            "SYS: 📊 voice-latency "
            f"stt={stt_ms if stt_ms is not None else '-'}ms "
            f"token={token_ms if token_ms is not None else '-'}ms "
            f"sentence={sent_ms if sent_ms is not None else '-'}ms "
            f"audio={audio_ms if audio_ms is not None else '-'}ms"
        )

    # ------------------------------------------------------------------
    # Remote dashboard (phone control via QR on port 8000)
    # ------------------------------------------------------------------

    def _make_remote_key(self):
        """Called from UI when user presses Remote Control."""
        if self._dashboard is None:
            self.ui.write_log(
                "SYS: Dashboard remoto indisponível. "
                "pip install fastapi 'uvicorn[standard]' cryptography qrcode[pil] python-multipart"
            )
            return None
        key    = self._dashboard.new_key()
        url    = self._dashboard.get_url()
        manual = self._dashboard.get_manual_url()
        return url, key, f"{url}/auto-login?key={key}", manual

    def _on_phone_connected(self) -> None:
        self.ui.write_log("SYS: Celular conectado via Remote Dashboard.")
        self.ui.notify_phone_connected()

    def _dashboard_broadcast(self, msg: dict) -> None:
        if self._dashboard_loop and self._dashboard:
            try:
                import asyncio
                asyncio.run_coroutine_threadsafe(
                    self._dashboard.broadcast(msg), self._dashboard_loop
                )
            except Exception:
                pass

    def _start_remote_dashboard(self) -> None:
        try:
            from dashboard.server import DashboardServer
            self._dashboard = DashboardServer()
            self._dashboard.set_connect_callback(self._on_phone_connected)

            def _run_dashboard() -> None:
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._dashboard_loop = loop

                async def _bridge_commands() -> None:
                    while True:
                        text = await self._dashboard._command_queue.get()
                        self._dashboard_cmd_sync.put(text)

                async def _bridge_phone_audio() -> None:
                    while True:
                        item = await self._dashboard._phone_audio_queue.get()
                        pcm  = item.get("data", b"")
                        if pcm:
                            self._phone_pcm_sync.put(pcm)

                async def _main() -> None:
                    asyncio.create_task(_bridge_commands())
                    asyncio.create_task(_bridge_phone_audio())
                    await self._dashboard.serve()

                try:
                    loop.run_until_complete(_main())
                except Exception as e:
                    print(f"[Dashboard] Stopped: {e}")

            threading.Thread(target=_run_dashboard, daemon=True, name="dashboard").start()
            threading.Thread(
                target=self._dashboard_command_loop, daemon=True, name="dash-cmd"
            ).start()
            threading.Thread(
                target=self._phone_audio_worker, daemon=True, name="phone-audio"
            ).start()
            ip = self._dashboard._ip
            self.ui.write_log(f"SYS: Remote Dashboard em http://{ip}:8000")
            self.ui.write_log("SYS: Clique ◉ REMOTE CONTROL para gerar QR code.")
        except Exception as e:
            self._dashboard = None
            self.ui.write_log(f"WARN: Remote Dashboard — {e}")

    def _dashboard_command_loop(self) -> None:
        while True:
            try:
                text = self._dashboard_cmd_sync.get(timeout=0.5)
            except queue.Empty:
                continue
            text = (text or "").strip()
            if not text:
                continue
            self.ui.write_log(f"[Celular]: {text}")
            self._dashboard_broadcast({"type": "log", "speaker": "user", "text": text})
            self._process_message(text, from_voice=False)

    def _phone_audio_worker(self) -> None:
        """Transcribe phone mic PCM bursts (WebSocket from mobile browser)."""
        buf: list[bytes] = []
        last = time.time()
        while True:
            try:
                pcm = self._phone_pcm_sync.get(timeout=0.25)
                self._phone_active = True
                buf.append(pcm)
                last = time.time()
            except queue.Empty:
                if not buf:
                    if self._phone_active and time.time() - last > 1.5:
                        self._phone_active = False
                    continue
                if time.time() - last < 0.9:
                    continue
                merged = b"".join(buf)
                buf = []
                self._phone_active = False
                if len(merged) < 3200 or self._stt is None:
                    continue
                try:
                    import numpy as np
                    audio = np.frombuffer(merged, dtype=np.int16).astype(np.float32) / 32767.0
                    t0 = time.time()
                    text = self._stt.transcribe(audio)
                    stt_ms = int((time.time() - t0) * 1000)
                    if text.strip():
                        self.ui.write_log(f"SYS: 🎤 Celular STT {stt_ms}ms")
                        self._dashboard_broadcast(
                            {"type": "log", "speaker": "user", "text": text}
                        )
                        self._process_message(text.strip(), from_voice=True)
                except Exception as e:
                    self.ui.write_log(f"ERR: Phone STT — {e}")

    # ------------------------------------------------------------------
    # Wake word detection
    # ------------------------------------------------------------------

    def _check_wake_word(self, text: str) -> tuple[bool, str]:
        """
        Check if text contains the wake word "Jarvis".
        Returns (is_wake_word_present, command_after_wake_word).
        
        Examples:
            "Jarvis, qual é a temperatura?" → (True, "qual é a temperatura?")
            "Jarvis abra o WhatsApp" → (True, "abra o WhatsApp")
            "Olá, como vai?" → (False, "")
        """
        if not text:
            return False, ""
        
        text_lower = text.lower().strip()
        
        for variant in self.WAKE_PREFIX_VARIANTS:
            for pattern in (f"{variant},", f"{variant} ", f"{variant}!", f"{variant}?"):
                if text_lower.startswith(pattern):
                    command = text[len(pattern):].strip()
                    return True, command
            if text_lower.startswith(variant):
                command = text[len(variant):].strip().lstrip(",.!?;:")
                return True, command

        # Check for wake word variants
        for variant in self.WAKE_WORD_VARIANTS:
            # Look for wake word at the beginning or after a pause
            patterns = [
                f"{variant},",           # "jarvis,"
                f"{variant} ",           # "jarvis "
                f"{variant}!",           # "jarvis!"
                f"{variant}?",          # "jarvis?"
                f"{variant}:",           # "jarvis:"
            ]
            
            for pattern in patterns:
                if text_lower.startswith(pattern):
                    command = text[len(pattern):].strip()
                    return True, command
        
        # Check if wake word appears anywhere in the text (with some flexibility)
        for variant in self.WAKE_WORD_VARIANTS:
            if variant in text_lower:
                # Find the position after the wake word
                idx = text_lower.find(variant)
                after_wake = text[idx + len(variant):].strip()

                # Remove leading punctuation if present
                if after_wake and after_wake[0] in ",.!?;:":
                    after_wake = after_wake[1:].strip()

                if after_wake:
                    return True, after_wake
                else:
                    # Just the wake word without command
                    return True, ""

        # Fuzzy fallback — Whisper badly mishears the short English name "Jarvis"
        # (seen: "AirGerves", "service", "travis"...). Compare each leading token
        # against the variants with a similarity ratio so near-misses still wake.
        import difflib
        words = [w.strip(",.!?;:") for w in text_lower.split()]
        for i, word in enumerate(words[:3]):
            if not word or len(word) < self._WAKE_FUZZY_MIN_LEN:
                continue
            score = max(
                difflib.SequenceMatcher(None, word, v).ratio()
                for v in self.WAKE_WORD_VARIANTS
            )
            if score >= self._WAKE_FUZZY_THRESHOLD:
                # Rebuild the command from the original-cased words after the match.
                command = " ".join(text.split()[i + 1:]).strip()
                if command and command[0] in ",.!?;:":
                    command = command[1:].strip()
                return True, command

        return False, ""

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    def _build_system_prompt(self, chat_mode: bool = False) -> str:
        now = datetime.now()
        time_ctx = f"[NOW] {now.strftime('%A, %d %b %Y %H:%M')}"

        if chat_mode:
            memory  = load_memory()
            mem_str = format_memory_for_prompt(memory)
            parts = [_CHAT_SYSTEM_PROMPT, time_ctx]
            if mem_str:
                parts.insert(1, mem_str)
            return "\n\n".join(parts)

        # ── ORDER MATTERS for Ollama KV prefix caching (local Ollama only) ─
        sys_p   = _load_system_prompt()
        memory  = load_memory()
        mem_str = format_memory_for_prompt(memory)
        parts = [sys_p]
        if mem_str:
            parts.append(mem_str)
        parts.append(time_ctx)
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Speaking state & TTS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # TTS queue worker — plays sentences sequentially, no overlaps
    # ------------------------------------------------------------------

    def _tts_worker(self) -> None:
        # Block until TTS engine is loaded.  Queued items are preserved
        # and played immediately once loading completes — nothing is lost.
        self._tts_ready.wait(timeout=120)

        while True:
            item = self._tts_queue.get()
            if isinstance(item, tuple):
                text, turn_id = item
            else:
                text, turn_id = item, None
            try:
                if turn_id is not None and not self._is_turn_active(turn_id):
                    continue
                if text and self._tts:
                    with self._speaking_lock:
                        self._speaking = True
                    self.ui.set_state("SPEAKING")
                    self._tts.speak(
                        text,
                        on_start=lambda tid=turn_id: self._on_tts_start_for_turn(tid),
                    )
            except Exception as e:
                print(f"[TTS] speak error: {e}")
            finally:
                self._tts_queue.task_done()
                if self._tts_queue.empty():
                    with self._speaking_lock:
                        self._speaking = False
                    # Keep a short echo guard without making follow-ups feel sluggish.
                    _cd = (
                        VOICE_SESSION_ECHO_GUARD_SEC
                        if self._in_voice_session()
                        else DEFAULT_ECHO_GUARD_SEC
                    )
                    self._listen_blocked_until = time.time() + _cd
                    if not self.ui.muted:
                        self.ui.set_state("LISTENING")

    def set_speaking(self, value: bool) -> None:
        with self._speaking_lock:
            self._speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def speak(self, text: str, *, turn_id: int | None = None) -> None:
        if not text or not self._tts:
            return
        with self._speaking_lock:
            self._speaking = True
        self._tts_queue.put((text, turn_id))

    def speak_error(self, tool_name: str, error) -> None:
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"{tool_name} encountered an error.")

    def _confirm_tool(self, name: str, args: dict) -> bool:
        """Show a blocking Qt confirmation dialog. Returns True if approved."""
        try:
            from PyQt6.QtWidgets import QMessageBox, QApplication
            action_str = f"{name}"
            if args:
                key_args = {k: v for k, v in args.items() if k not in ("player",)}
                action_str += f"\n{key_args}"
            app = QApplication.instance()
            if app is None:
                return True  # no UI — allow by default
            box = QMessageBox()
            box.setWindowTitle("Jarvis — Confirm Action")
            box.setText(f"Confirm: {action_str}?")
            box.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            box.setDefaultButton(QMessageBox.StandardButton.No)
            # 15s auto-reject via singleShot
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(15_000, lambda: box.reject())
            return box.exec() == QMessageBox.StandardButton.Yes
        except Exception as e:
            print(f"[Confirm] Dialog error: {e} — allowing by default")
            return True

    # ------------------------------------------------------------------
    # Live reconfigure (called when user clicks Apply in Configure panel)
    # ------------------------------------------------------------------

    def reconfigure(self, new_config: dict) -> None:
        """Non-blocking: spawns a background thread to install + reload."""
        threading.Thread(
            target=self._do_reconfigure, args=(new_config,), daemon=True
        ).start()

    def _do_reconfigure(self, new_config: dict) -> None:
        old_stt_engine = self._config.get("stt_engine", "whisper").lower()
        old_llm_model  = self._config.get("llm_model", "")
        new_stt_engine = new_config.get("stt_engine", "whisper").lower()
        self._config = new_config

        # Install any packages required by the new config
        try:
            from core.installer import install_for_config
            install_for_config(new_config, log=self.ui.write_log)
        except Exception as e:
            self.ui.write_log(f"ERR: Dependency install — {e}")

        # TTS: always hot-reload (runs in queue worker, safe to swap)
        try:
            from core.tts import create_tts_player
            self._tts = create_tts_player(new_config)
            self._tts_ready.set()   # ensure worker isn't blocked
            self.ui.write_log("SYS: TTS reconfigured.")
        except Exception as e:
            self.ui.write_log(f"ERR: TTS reconfigure — {e}")

        # STT: hot-reload if same engine type; full restart needed if engine changed
        if old_stt_engine == new_stt_engine:
            try:
                stt_language = new_config.get("stt_language", "auto")
                if new_stt_engine == "vosk":
                    from core.stt import VoskSTT
                    self._stt = VoskSTT(new_config.get("vosk_model_path"), language=stt_language)
                elif new_stt_engine == "deepgram":
                    from core.stt_deepgram import DeepgramSTT
                    self._stt = DeepgramSTT(
                        api_key=new_config.get("deepgram_api_key"),
                        model=new_config.get("deepgram_model", "nova-2"),
                        language=stt_language,
                    )
                else:
                    from core.stt import WhisperSTT
                    self._stt = WhisperSTT(new_config.get("stt_model", "base"), language=stt_language)
                self.ui.write_log("SYS: STT reconfigured.")
            except Exception as e:
                self.ui.write_log(f"ERR: STT reconfigure — {e}")
        else:
            self.ui.write_log("SYS: STT engine changed — restart required.")

        # LLM warmup if model changed
        if new_config.get("llm_model", "") != old_llm_model:
            self.ui.write_log("SYS: Warming up new LLM model…")
            from core.llm_client import warmup_model
            warmup_model()
            self.ui.write_log("SYS: New LLM model ready.")

        if old_stt_engine == new_stt_engine:
            self.speak("Configuration applied.")
        else:
            self.speak("LLM and TTS updated. Restart for speech engine change.")

    # ------------------------------------------------------------------
    # Text command (from UI input box)
    # ------------------------------------------------------------------

    def _on_text_command(self, text: str) -> None:
        self._mark_user_activity()
        self._text_queue.put(text)

    # ------------------------------------------------------------------
    # Tool execution (routing unchanged from original)
    # ------------------------------------------------------------------

    def _execute_tool(self, name: str, args: dict) -> str:
        print(f"[JARVIS] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")

        # save_memory is handled silently
        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return "__SILENT__"

        # Confirmation matrix — ask user before running sensitive tools
        _cfg = _load_config()
        _CONFIRM_TOOLS = set(_cfg.get(
            "tool_requires_confirm",
            ["send_message", "app_installer", "computer_control"],
        ))
        if name in _CONFIRM_TOOLS or (
            name == "file_controller" and args.get("action") in ("delete", "trash")
        ):
            if not self._confirm_tool(name, args):
                return "Action cancelled by user."

        _tool_timeout = _cfg.get("tool_timeout_sec", 45)

        def _dispatch() -> str:
            if name == "open_app":
                r = open_app(parameters=args, response=None, player=self.ui)
                return r or f"Opened {args.get('app_name')}."

            elif name == "weather_report":
                r = weather_action(parameters=args, player=self.ui)
                return r or "Weather delivered."

            elif name == "browser_control":
                r = browser_control(parameters=args, player=self.ui)
                return r or "Done."

            elif name == "file_controller":
                r = file_controller(parameters=args, player=self.ui)
                return r or "Done."

            elif name == "send_message":
                r = send_message(parameters=args, response=None, player=self.ui, session_memory=None)
                return r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = reminder(parameters=args, response=None, player=self.ui)
                return r or "Reminder set."

            elif name == "youtube_video":
                r = youtube_video(parameters=args, response=None, player=self.ui)
                return r or "Done."

            elif name == "screen_process":
                r = screen_process(parameters=args, response=None, player=self.ui, session_memory=None)
                return r if isinstance(r, str) and r else "Screen analyzed."

            elif name == "computer_settings":
                r = computer_settings(parameters=args, response=None, player=self.ui)
                return r or "Done."

            elif name == "desktop_control":
                r = desktop_control(parameters=args, player=self.ui)
                return r or "Done."

            elif name == "code_helper":
                r = code_helper(parameters=args, player=self.ui, speak=self.speak)
                return r or "Done."

            elif name == "dev_agent":
                r = dev_agent(parameters=args, player=self.ui, speak=self.speak)
                return r or "Done."

            elif name == "agent_task":
                from agent.task_queue import get_queue, TaskPriority
                priority_map = {
                    "low": TaskPriority.LOW,
                    "normal": TaskPriority.NORMAL,
                    "high": TaskPriority.HIGH,
                }
                priority = priority_map.get(
                    args.get("priority", "normal").lower(), TaskPriority.NORMAL
                )
                def _on_step(step_num: int, tool: str, desc: str) -> None:
                    self.ui.write_log(f"SYS: ⚙ step {step_num} — {tool}: {desc[:60]}")

                task_id = get_queue().submit(
                    goal=args.get("goal", ""),
                    priority=priority,
                    speak=self.speak,
                    on_step_start=_on_step,
                )
                return f"Task started (ID: {task_id})."

            elif name == "web_search":
                r = web_search_action(parameters=args, player=self.ui)
                return r or "Done."

            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = file_processor(parameters=args, player=self.ui, speak=self.speak)
                return r or "Done."

            elif name == "computer_control":
                r = computer_control(parameters=args, player=self.ui)
                return r or "Done."

            elif name == "game_updater":
                r = game_updater(parameters=args, player=self.ui, speak=self.speak)
                return r or "Done."

            elif name == "flight_finder":
                r = flight_finder(parameters=args, player=self.ui)
                return r or "Done."

            # New tools
            elif name == "clipboard":
                from actions.clipboard_tool import clipboard_tool
                r = clipboard_tool(parameters=args, player=self.ui)
                return r or "Done."

            elif name == "email_tool":
                from actions.email_tool import email_tool
                r = email_tool(parameters=args, player=self.ui, speak=self.speak)
                return r or "Done."

            elif name == "spotify":
                from actions.spotify_tool import spotify_tool
                r = spotify_tool(parameters=args, player=self.ui, speak=self.speak)
                return r or "Done."

            elif name == "notes":
                from actions.notes_tool import notes_tool
                r = notes_tool(parameters=args, player=self.ui, speak=self.speak)
                return r or "Done."

            elif name == "translator":
                from actions.translator_tool import translator_tool
                r = translator_tool(parameters=args, player=self.ui, speak=self.speak)
                return r or "Done."

            elif name == "timer":
                from actions.timer_tool import timer_tool
                r = timer_tool(parameters=args, player=self.ui, speak=self.speak)
                return r or "Done."

            elif name == "calculator":
                from actions.calculator_tool import calculator_tool
                r = calculator_tool(parameters=args, player=self.ui, speak=self.speak)
                return r or "Done."

            elif name == "notify":
                from actions.notify_tool import notify_tool
                r = notify_tool(parameters=args, player=self.ui)
                return r or "Done."

            elif name == "app_installer":
                from actions.app_installer import app_installer
                r = app_installer(parameters=args, player=self.ui, speak=self.speak)
                return r or "Done."

            elif name == "calendar":
                from actions.calendar_tool import calendar_tool
                r = calendar_tool(parameters=args, player=self.ui)
                return r or "Done."

            elif name == "visual_web":
                from actions.visual_web import visual_web
                r = visual_web(parameters=args, player=self.ui, speak=self.speak)
                return r or "Done."

            elif name == "smart_home":
                from actions.kasa_tool import kasa_tool
                r = kasa_tool(parameters=args, player=self.ui)
                return r or "Done."

            return f"Unknown tool: {name}"

        # shutdown_jarvis bypasses timeout — must run in main thread context
        if name == "shutdown_jarvis":
            self.ui.write_log("SYS: Shutdown requested.")

            def _shutdown():
                import time as _t, os as _os
                self.speak("Goodbye.")
                _t.sleep(2.5)
                _os._exit(0)

            threading.Thread(target=_shutdown, daemon=True).start()
            return "Shutting down."

        result = "Done."
        _t0 = time.time()
        try:
            with ThreadPoolExecutor(max_workers=1) as _ex:
                _fut = _ex.submit(_dispatch)
                try:
                    result = _fut.result(timeout=_tool_timeout)
                except FuturesTimeout:
                    _fut.cancel()
                    msg = f"Tool '{name}' timed out after {_tool_timeout}s, sir."
                    self.speak(msg)
                    result = msg

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        _duration_ms = int((time.time() - _t0) * 1000)
        log.info("Tool %s completed in %dms", name, _duration_ms)

        # Persist tool call to SQLite
        try:
            from memory.conversation_db import add_message, add_tool_call
            msg_id = add_message(self._conv_id, "tool", f"[{name}] {str(result)[:200]}")
            add_tool_call(msg_id, name, args, str(result)[:500], _duration_ms)
        except Exception:
            pass

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")
        return result

    # ------------------------------------------------------------------
    # Persistence helper
    # ------------------------------------------------------------------

    def _persist_assistant(self, content: str) -> None:
        """Save assistant response to SQLite conversation history."""
        try:
            from memory.conversation_db import add_message
            add_message(self._conv_id, "assistant", content)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # LLM processing loop
    # ------------------------------------------------------------------

    def _needs_tools(self, user_text: str) -> bool:
        """Skip tool schema for casual chat — prevents Groq tool_use_failed errors."""
        return bool(self._ACTION_RE.search(user_text))

    def _process_message(self, user_text: str, *, from_voice: bool = False, stt_ms: float = 0.0) -> None:
        """
        Full turn: user_text → LLM stream → TTS (overlapped) → tool execution

        Streaming TTS: sentence events are piped to the TTS queue AS they
        arrive from the LLM, so Kokoro starts synthesising sentence 1 while
        the LLM is still generating sentence 2.  This cuts perceived latency
        from (LLM_total + TTS_total) down to roughly max(LLM_total, TTS_total).

        Tool-call responses never emit sentence events, so the TTS overlap
        only kicks in for pure conversational replies — which is exactly when
        it matters most.
        """
        voice_turn_id = self._activate_voice_turn(stt_ms) if from_voice else None
        with self._turn_lock:
            self._process_message_locked(
                user_text,
                from_voice=from_voice,
                stt_ms=stt_ms,
                voice_turn_id=voice_turn_id,
            )

    def _process_message_locked(
        self,
        user_text: str,
        *,
        from_voice: bool = False,
        stt_ms: float = 0.0,
        voice_turn_id: int | None = None,
    ) -> None:
        self.ui.set_state("THINKING")
        prefix = "Voz" if from_voice else "You"
        self.ui.write_log(f"{prefix}: {user_text}")
        self.ui.add_history("user", user_text)
        if from_voice and voice_turn_id is None:
            voice_turn_id = self._activate_voice_turn(stt_ms)

        self._conversation.append({"role": "user", "content": user_text})

        # Persist to SQLite
        from memory.conversation_db import add_message
        add_message(self._conv_id, "user", user_text)

        MAX_HISTORY = 6 if not self._needs_tools(user_text) else 10
        if len(self._conversation) > MAX_HISTORY:
            self._conversation = self._conversation[-MAX_HISTORY:]

        needs_tools    = self._needs_tools(user_text)
        chat_mode      = not needs_tools
        tools_for_turn = OLLAMA_TOOLS if needs_tools else None

        messages = [
            {"role": "system", "content": self._build_system_prompt(chat_mode=chat_mode)}
        ] + list(self._conversation)

        # Tools whose output needs a second LLM round to summarise/interpret.
        # Everything else returns a user-ready string → speak directly.
        _NEEDS_LLM_ROUND = {"web_search", "screen_process", "agent_task"}

        MAX_TOOL_ROUNDS = 6
        from core.llm_client import get_chat_llm_config, get_fast_llm_model, get_power_llm_model
        chat_url, chat_model, chat_provider = get_chat_llm_config()
        llm_model = get_power_llm_model() if needs_tools else chat_model

        _t0 = time.time()
        _replied = False
        _first_chunk_logged = False
        _first_sentence_logged = False

        for _round in range(MAX_TOOL_ROUNDS):
            if from_voice and not self._is_turn_active(voice_turn_id):
                self.ui.write_log("SYS: ↻ voice turn superseded by newer command.")
                return
            final_content    = ""
            final_tool_calls: list = []
            # Sentences already queued to TTS during streaming (may be empty
            # for tool-call rounds where the model emits no content).
            _streamed: list[str] = []

            # After tool execution, always re-enable tools for follow-up rounds.
            round_tools = tools_for_turn if _round == 0 else OLLAMA_TOOLS

            try:
                _round_model = llm_model if _round == 0 else get_power_llm_model()
                _use_chat_route = chat_mode and _round == 0
                for event in call_llm_stream(
                    messages, round_tools,
                    model=_round_model,
                    provider=chat_provider if _use_chat_route else None,
                    base_url=chat_url if _use_chat_route else None,
                ):
                    if from_voice and not self._is_turn_active(voice_turn_id):
                        self.ui.write_log("SYS: ↻ voice stream cancelled (newer turn active).")
                        return
                    if event["type"] == "chunk":
                        if _round == 0 and not _first_chunk_logged:
                            _first_chunk_logged = True
                            _lat = int((time.time() - _t0) * 1000)
                            self._set_voice_metric(voice_turn_id, "first_token_ms", _lat)
                            _route = chat_provider if _use_chat_route else "power"
                            self.ui.write_log(f"SYS: ⚡ first token {_lat}ms ({_route})")
                    elif event["type"] == "sentence":
                        if _round == 0 and not _first_sentence_logged:
                            _first_sentence_logged = True
                            _lat = int((time.time() - _t0) * 1000)
                            self._set_voice_metric(voice_turn_id, "first_sentence_ms", _lat)
                            self.ui.write_log(f"SYS: 🔊 first sentence {_lat}ms")
                        # Speak every streamed sentence to avoid partial voice replies.
                        self.speak(event["text"], turn_id=voice_turn_id)
                        _streamed.append(event["text"])
                        self.ui.stream_sentence(event["text"])
                    elif event["type"] == "done":
                        final_content    = event["content"]
                        final_tool_calls = event["tool_calls"]
            except RuntimeError as e:
                self.speak_error("LLM", e)
                return

            # ── No tool calls: pure conversational reply ─────────────────────
            if not final_tool_calls:
                reply = final_content or (" ".join(_streamed) if _streamed else "")
                if reply:
                    _replied = True
                    assistant_msg = {"role": "assistant", "content": reply}
                    messages.append(assistant_msg)
                    self._conversation.append(assistant_msg)
                    if not _streamed:
                        self.ui.write_log(f"Jarvis: {reply}")
                        self.speak(reply, turn_id=voice_turn_id)
                    self.ui.add_history("jarvis", reply)
                    self._persist_assistant(reply)
                    self._dashboard_broadcast(
                        {"type": "log", "speaker": "jarvis", "text": reply}
                    )
                    break
                self.ui.write_log("ERR: LLM — empty reply (no tools)")
                break

            # ── Tool calls present ────────────────────────────────────────────
            assistant_msg = {
                "role":       "assistant",
                "content":    final_content or "",
                "tool_calls": final_tool_calls,
            }
            messages.append(assistant_msg)
            self._conversation.append(assistant_msg)

            # ── Fast path: save_memory + verbal content in same round ────────
            _only_memory = all(
                tc.get("function", {}).get("name") == "save_memory"
                for tc in final_tool_calls
            )
            if _only_memory and final_content:
                _replied = True
                for tc in final_tool_calls:
                    fn    = tc.get("function", {})
                    targs = fn.get("arguments", {})
                    if isinstance(targs, str):
                        try:
                            targs = json.loads(targs)
                        except Exception:
                            targs = {}
                    self._execute_tool("save_memory", targs)
                assistant_msg2 = {"role": "assistant", "content": final_content}
                messages.append(assistant_msg2)
                self._conversation.append(assistant_msg2)
                self.ui.write_log(f"Jarvis: {final_content}")
                self.ui.add_history("jarvis", final_content)
                self._persist_assistant(final_content)
                if not _streamed:
                    self.speak(final_content, turn_id=voice_turn_id)
                break

            # ── Execute tools ─────────────────────────────────────────────────
            all_silent    = True
            _tool_results: list[tuple[str, str]] = []

            for tc in final_tool_calls:
                fn    = tc.get("function", {})
                tname = fn.get("name", "")
                targs = fn.get("arguments", {})
                if isinstance(targs, str):
                    try:
                        targs = json.loads(targs)
                    except Exception:
                        targs = {}

                tc_id = tc.get("id", "")
                self.ui.write_log(f"SYS: ▶ {tname}")
                result = self._execute_tool(tname, targs)

                if result != "__SILENT__":
                    all_silent = False
                    _tool_results.append((tname, result))

                tool_msg: dict = {
                    "role":    "tool",
                    "content": "Done." if result == "__SILENT__" else str(result),
                }
                if tc_id:
                    tool_msg["tool_call_id"] = tc_id

                messages.append(tool_msg)
                self._conversation.append(tool_msg)

            # ── Fast-ack: every call was save_memory (silent) ────────────────
            if all_silent:
                _saved_name: str | None = None
                for _tc in final_tool_calls:
                    _fn = _tc.get("function", {})
                    if _fn.get("name") == "save_memory":
                        _a = _fn.get("arguments", {})
                        if isinstance(_a, str):
                            try:
                                _a = json.loads(_a)
                            except Exception:
                                _a = {}
                        if isinstance(_a, dict) and _a.get("key") == "name" and _a.get("value"):
                            _saved_name = str(_a["value"])
                            break
                _ack = f"Got it, {_saved_name}." if _saved_name else "Noted."
                _amsg = {"role": "assistant", "content": _ack}
                messages.append(_amsg)
                self._conversation.append(_amsg)
                self.ui.write_log(f"Jarvis: {_ack}")
                self.ui.add_history("jarvis", _ack)
                self._persist_assistant(_ack)
                self.speak(_ack, turn_id=voice_turn_id)
                _replied = True
                break

            # ── Direct-result: speak tool output, skip LLM round ────────────
            if _tool_results and not any(n in _NEEDS_LLM_ROUND for n, _ in _tool_results):
                _, _reply = _tool_results[-1]
                _amsg = {"role": "assistant", "content": _reply}
                messages.append(_amsg)
                self._conversation.append(_amsg)
                self.ui.write_log(f"Jarvis: {_reply}")
                self.ui.add_history("jarvis", _reply)
                self._persist_assistant(_reply)
                self.speak(_reply, turn_id=voice_turn_id)
                _replied = True
                break

        if not _replied:
            self.ui.write_log("ERR: LLM — empty response after all rounds")
            self.speak(
                "Desculpe senhor, tive um problema ao processar. Pode repetir?",
                turn_id=voice_turn_id,
            )

        if from_voice and _replied:
            self._extend_voice_session()

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

    # ------------------------------------------------------------------
    # STT listening loops
    # ------------------------------------------------------------------

    def _handle_voice_transcript(self, text: str, stt_ms: float = 0) -> None:
        """Route a finalized voice transcript through wake-word logic."""
        text = text.strip()
        if not text:
            return

        now = time.time()
        norm = self._normalize_transcript_for_dedupe(text)
        if norm and norm == self._last_voice_transcript_norm and (now - self._last_voice_transcript_at) < 3.0:
            self.ui.write_log(f"SKIP: '{text}' (transcrição duplicada)")
            return
        self._last_voice_transcript_norm = norm
        self._last_voice_transcript_at = now

        if self._mic_blocked():
            self.ui.write_log(f"SKIP: '{text}' (echo guard)")
            return

        if stt_ms > 0:
            self.ui.write_log(f"SYS: 🎤 STT {int(stt_ms)}ms")

        if self._continuous_mode:
            self.ui.write_log(f"USER: '{text}'")
            self._mark_user_activity()
            self._process_message(text, from_voice=True, stt_ms=stt_ms)
            return

        # After "Jarvis, ..." keep the conversation open for ~45s (no wake word needed).
        if self._in_voice_session():
            self.ui.write_log(f"VOZ (sessão): '{text}'")
            self._extend_voice_session()
            self._mark_user_activity()
            self._process_message(text, from_voice=True, stt_ms=stt_ms)
            return

        is_wake, command = self._check_wake_word(text)
        if is_wake:
            self._extend_voice_session()
            if command:
                self.ui.write_log(f"WAKE: '{text}' → '{command}'")
                self._mark_user_activity()
                self._process_message(command, from_voice=True, stt_ms=stt_ms)
            else:
                self.ui.write_log(f"WAKE: '{text}' (aguardando comando)")
                self.speak("Sim?")
        else:
            self.ui.write_log(f"SKIP: '{text}' (diga Jarvis para iniciar)")

    def _listen_deepgram(self) -> None:
        """Mic → Deepgram live WebSocket → Wake Word → LLM (lowest latency)."""
        from core.stt_deepgram import DeepgramLiveSTT

        _last_interim = ""
        _last_interim_log_at = 0.0
        _voice_q: queue.Queue = queue.Queue()

        def _on_final(transcript: str, stt_ms: float) -> None:
            _voice_q.put((transcript, stt_ms))

        def _on_interim(transcript: str) -> None:
            nonlocal _last_interim, _last_interim_log_at
            now = time.time()
            changed = transcript != _last_interim
            grew = len(transcript) >= len(_last_interim) + 8
            cooled_down = (now - _last_interim_log_at) >= 0.8
            if changed and len(transcript) > 3 and (grew or cooled_down):
                _last_interim = transcript
                _last_interim_log_at = now
                self.ui.write_log(f"SYS: 🎤 …{transcript[-40:]}")

        try:
            live = DeepgramLiveSTT(
                on_final=_on_final,
                on_interim=_on_interim,
                api_key=self._config.get("deepgram_api_key"),
                model=self._config.get("deepgram_model", "nova-2"),
                language=self._config.get("stt_language", "pt"),
                endpointing_ms=int(self._config.get("deepgram_endpointing_ms", 300)),
                utterance_end_ms=int(self._config.get("deepgram_utterance_end_ms", 1000)),
            )
            live.start()
        except Exception as e:
            self.ui.write_log(f"WARN: Deepgram live failed ({e}) — using batch STT")
            self._listen_whisper()
            return

        def callback(indata, frames, time_info, status):
            if not self._mic_blocked() and not self.ui.muted:
                live.feed_float(indata.flatten())

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE_IN,
                channels=CHANNELS,
                dtype="float32",
                blocksize=BLOCK_SIZE,
                callback=callback,
            ):
                mode_label = "Continuous" if self._continuous_mode else "Wake word: JARVIS"
                self.ui.write_log(
                    f"SYS: Mic active (DEEPGRAM LIVE) — {mode_label}"
                )
                while True:
                    try:
                        transcript, stt_ms = _voice_q.get(timeout=0.05)
                        self._handle_voice_transcript(transcript, stt_ms)
                    except queue.Empty:
                        pass
        except Exception as e:
            print(f"[STT-Deepgram] Mic error: {e}")
            traceback.print_exc()
        finally:
            live.close()

    def _listen_whisper(self) -> None:
        """Mic → VAD → Whisper/Deepgram batch → Wake Word Check → LLM loop."""
        vad = _VADBuffer()
        q: queue.Queue = queue.Queue(maxsize=200)
        _stt_busy = threading.Event()

        def callback(indata, frames, time_info, status):
            if not self._mic_blocked() and not self.ui.muted:
                try:
                    q.put_nowait(indata.copy())
                except queue.Full:
                    pass

        def _run_stt(audio: np.ndarray, t0: float) -> None:
            try:
                if self._stt is None:
                    return
                text = self._stt.transcribe(audio)
                stt_ms = (time.time() - t0) * 1000
                self._handle_voice_transcript(text, stt_ms)
            except Exception as e:
                self.ui.write_log(f"ERR: STT — {e}")
            finally:
                _stt_busy.clear()

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE_IN,
                channels=CHANNELS,
                dtype="float32",
                blocksize=BLOCK_SIZE,
                callback=callback,
            ):
                mode_label = "Continuous" if self._continuous_mode else "Wake word: JARVIS"
                stt_label  = self._config.get("stt_engine", "whisper").upper()
                self.ui.write_log(f"SYS: Mic active ({stt_label} STT) — {mode_label}")
                while True:
                    try:
                        chunk = q.get(timeout=0.1)
                        audio = vad.process(chunk.flatten())
                        if audio is not None and not _stt_busy.is_set():
                            _stt_busy.set()
                            self.ui.set_state("THINKING")
                            t0 = time.time()
                            self._stt_executor.submit(_run_stt, audio, t0)
                    except queue.Empty:
                        pass
        except Exception as e:
            print(f"[STT-Whisper] Mic error: {e}")
            traceback.print_exc()

    def _listen_vosk(self) -> None:
        """Mic → Vosk streaming → Wake Word Check → LLM loop."""
        q: queue.Queue = queue.Queue(maxsize=200)

        def callback(indata, frames, time_info, status):
            if not self._mic_blocked() and not self.ui.muted:
                try:
                    q.put_nowait(indata.copy())
                except queue.Full:
                    pass

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE_IN,
                channels=CHANNELS,
                dtype="int16",
                blocksize=4096,
                callback=callback,
            ):
                mode_label = "Continuous" if self._continuous_mode else "Wake word: JARVIS"
                self.ui.write_log(f"SYS: Mic active (Vosk STT) — {mode_label}")
                while True:
                    try:
                        chunk = q.get(timeout=0.1)
                        text, is_final = self._stt.process_chunk(chunk.tobytes())
                        if is_final and text.strip():
                            self._handle_voice_transcript(text)
                    except queue.Empty:
                        pass
        except Exception as e:
            print(f"[STT-Vosk] Mic error: {e}")
            traceback.print_exc()

    # ------------------------------------------------------------------
    # Text command loop (UI input box)
    # ------------------------------------------------------------------

    def _text_command_loop(self) -> None:
        while True:
            try:
                text = self._text_queue.get(timeout=0.5)
                if text.strip():
                    self._process_message(text)
            except queue.Empty:
                pass

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        """
        Startup strategy — optimised for minimum time-to-interactive:

        1. LLM warmup + STT load  →  parallel, fast (~3s)
        2. TTS load               →  parallel, slow (~20s for Kokoro)
        3. Wait only for (1)      →  go online immediately
        4. TTS finishes in BG     →  queued speech plays automatically
        """
        try:
            self.ui.on_reconfigure = self.reconfigure

            # ── Ollama ────────────────────────────────────────────────────
            from core.llm_client import ensure_ollama_running, warmup_model, get_llm_provider, _provider_label
            _prov = get_llm_provider()
            self.ui.write_log(f"SYS: Checking {_provider_label(_prov)}…")
            if ensure_ollama_running():
                self.ui.write_log(f"SYS: {_provider_label(_prov)} OK.")
            else:
                if _prov == "ollama":
                    self.ui.write_log("ERR: Ollama unavailable — run: ollama serve")
                else:
                    self.ui.write_log(f"ERR: {_provider_label(_prov)} unreachable — check API key / URL")

            # ── Config ────────────────────────────────────────────────────
            stt_engine   = self._config.get("stt_engine",   "whisper").lower()
            stt_language = self._config.get("stt_language", "auto")
            stt_model    = self._config.get("stt_model",    "base")
            tts_engine   = self._config.get("tts_engine",   "edgetts").lower()

            # ── Startup progress panel ────────────────────────────────────
            self.ui.show_startup_panel()

            _warmup_done = threading.Event()
            _stt_done    = threading.Event()

            # ── LLM warmup thread ─────────────────────────────────────────
            def _do_warmup():
                try:
                    # Pass the STATIC system prompt so Ollama evaluates and caches
                    # its KV state during startup.  Real requests start with the same
                    # static prefix → Ollama reuses cached KV → first token <1 s
                    # instead of the ~17 s it takes to re-evaluate 300+ tokens cold.
                    static_prompt = _load_system_prompt()
                    warmup_model(system_prompt=static_prompt)
                    self.ui.write_log("SYS: LLM ready.")
                    self.ui.mark_startup_ready("llm")
                except Exception as e:
                    self.ui.write_log(f"ERR: LLM warmup — {e}")
                    self.ui.mark_startup_ready("llm", error=True)
                finally:
                    _warmup_done.set()

            # ── STT load thread ───────────────────────────────────────────
            def _do_stt():
                try:
                    self.ui.write_log(f"SYS: Loading {stt_engine.upper()} STT…")
                    if stt_engine == "vosk":
                        from core.stt import VoskSTT
                        self._stt = VoskSTT(
                            self._config.get("vosk_model_path"),
                            language=stt_language,
                        )
                    elif stt_engine == "deepgram":
                        from core.stt_deepgram import DeepgramSTT
                        self._stt = DeepgramSTT(
                            api_key=self._config.get("deepgram_api_key"),
                            model=self._config.get("deepgram_model", "nova-2"),
                            language=stt_language,
                        )
                    else:
                        from core.stt import WhisperSTT
                        self._stt = WhisperSTT(stt_model, language=stt_language)
                    self.ui.write_log("SYS: STT ready.")
                    self.ui.mark_startup_ready("stt")
                except Exception as e:
                    self.ui.write_log(f"ERR: STT — {e}")
                    self.ui.mark_startup_ready("stt", error=True)
                finally:
                    _stt_done.set()

            # ── TTS load thread — does NOT block going online ─────────────
            def _do_tts():
                try:
                    self.ui.write_log(f"SYS: Loading {tts_engine.upper()} TTS…")
                    if tts_engine == "kokoro":
                        self.ui.write_log("SYS: Kokoro — loading model + compiling JIT…")
                    from core.tts import create_tts_player
                    self._tts = create_tts_player(self._config)
                    self._tts_ready.set()          # unblock _tts_worker
                    self.ui.write_log("SYS: TTS ready.")
                    self.ui.mark_startup_ready("tts")
                    self.ui.set_startup_status("● All systems ready.")
                    self.ui.hide_startup_panel()
                    self.speak("Jarvis fully online.")
                except Exception as e:
                    import traceback as _tb; _tb.print_exc()
                    self.ui.write_log(f"ERR: TTS — {e}")
                    self.ui.mark_startup_ready("tts", error=True)
                    self._tts_ready.set()

            # Launch all three simultaneously
            self.ui.write_log("SYS: Loading systems in parallel…")
            threading.Thread(target=_do_warmup, daemon=True).start()
            threading.Thread(target=_do_stt,    daemon=True).start()
            threading.Thread(target=_do_tts,    daemon=True).start()

            # ── Wait ONLY for STT + LLM (fast) ────────────────────────────
            _warmup_done.wait(timeout=60)
            _stt_done.wait(timeout=60)

            # ── Face authentication (optional) ────────────────────────────
            if self._config.get("face_auth_enabled", False):
                from core.face_auth import FaceAuth
                auth = FaceAuth()
                ok = auth.start_session(speak=self.speak)
                if not ok:
                    self.ui.write_log("ERR: Face auth failed — shutting down.")
                    import os as _os; _os._exit(1)
                self.ui.write_log("SYS: Face auth passed.")

            # ── Go online immediately ──────────────────────────────────────
            from core.llm_client import get_chat_llm_config
            _cu, _cm, _cp = get_chat_llm_config()
            self.ui.write_log(f"SYS: Chat fast-path → {_cp} / {_cm}")
            self._start_remote_dashboard()
            self.ui.write_log("SYS: JARVIS online.")
            self.ui.set_state("LISTENING")
            self.ui.set_startup_status("● JARVIS online · Voice loading in background…")

            # Start web dashboard
            try:
                from web.dashboard import start_web_dashboard
                start_web_dashboard()
                self.ui.write_log("SYS: Web dashboard at http://localhost:5050")
            except Exception as e:
                self.ui.write_log(f"ERR: Web dashboard — {e}")

            threading.Thread(target=self._tts_worker,        daemon=True).start()
            threading.Thread(target=self._text_command_loop,  daemon=True).start()
            threading.Thread(target=self._proactive_worker,   daemon=True).start()

            # STT loop — blocks this thread forever
            if stt_engine == "vosk":
                self._listen_vosk()
            elif stt_engine == "deepgram":
                self._listen_deepgram()
            else:
                self._listen_whisper()

        except Exception as e:
            self.ui.write_log(f"ERR: Init failed — {e}")
            traceback.print_exc()


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main() -> None:
    # ── Global exception handler — prevents PyQt6 abort() on slot errors ──
    def _global_excepthook(exc_type, exc_value, exc_tb):
        import traceback
        if exc_type is SystemExit:
            raise exc_type
        traceback.print_exception(exc_type, exc_value, exc_tb)
    sys.excepthook = _global_excepthook

    # ── Pre-import torch in background immediately ─────────────────────────
    # By the time the TTS thread starts (~5s from now), torch will already
    # be in sys.modules — removing it from the TTS critical path entirely.
    def _preload_torch():
        try:
            import torch  # noqa: F401  (side-effect import only)
        except Exception:
            pass
    threading.Thread(target=_preload_torch, daemon=True).start()
    # ───────────────────────────────────────────────────────────────────────

    ui = JarvisUI("face.png")

    def runner():
        # 1. Wait until the user completes the setup overlay (first run)
        #    or config already exists (subsequent runs).
        ui.wait_for_api_key()

        # 2. Install any missing engine packages before loading engines.
        #    Progress is streamed to the log panel in real time.
        ui.write_log("SYS: Checking dependencies…")
        cfg = _load_config()
        _install_done = threading.Event()

        def _do_install():
            try:
                from core.installer import install_for_config
                install_for_config(cfg, log=ui.write_log)
            except Exception as e:
                ui.write_log(f"ERR: Dependency install — {e}")
            finally:
                _install_done.set()

        threading.Thread(target=_do_install, daemon=True).start()
        _install_done.wait()   # blocks runner thread; UI remains responsive

        # 3. Start the assistant (loads STT / TTS / LLM).
        jarvis = JarvisLocal(ui)
        try:
            jarvis.run()
        except KeyboardInterrupt:
            print("\n[MARK XL] Shutting down…")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()

if __name__ == "__main__":
    main()
