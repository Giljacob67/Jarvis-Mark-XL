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

# ── Bootstrap: auto-install base UI packages before anything else ──────────
# Uses only stdlib so it works even on a completely fresh Python install.
import importlib.util as _ilu
import subprocess      as _sp
import sys             as _sys

_BASE_PKGS = [
    ("PyQt6",       "PyQt6"),
    ("psutil",      "psutil"),
    ("numpy",       "numpy"),
    ("sounddevice", "sounddevice"),
    ("PIL",         "pillow"),
    ("requests",    "requests"),
]

def _bootstrap() -> None:
    need = [pkg for mod, pkg in _BASE_PKGS if _ilu.find_spec(mod) is None]
    if not need:
        return
    print(f"\n[MARK XL] First-run setup — installing: {', '.join(need)}")
    print("[MARK XL] This happens only once.\n")
    _sp.run([_sys.executable, "-m", "pip", "install", *need], check=True)
    print("\n[MARK XL] Base packages ready — restarting…\n")
    # Replace current process with a fresh one (picks up newly installed packages)
    _os.execv(_sys.executable, [_sys.executable] + _sys.argv)

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

# Diarization for continuous mode
try:
    from core.diarization import DiarizationProcessor, is_available as diarization_available
    _DIARIZATION_READY = True
except ImportError:
    _DIARIZATION_READY = False

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

# ---------------------------------------------------------------------------
# Tool declarations — imported from canonical source
# ---------------------------------------------------------------------------

from core.tools import TOOL_DECLARATIONS, OLLAMA_TOOLS, CORE_TOOLS

# Encrypted dashboard (optional — auto-installed)
try:
    from dashboard.server import DashboardServer, _DEPS_OK as _DASHBOARD_DEPS_OK
    _DASHBOARD_AVAILABLE = True
except ImportError:
    _DASHBOARD_AVAILABLE = False

# Phone mic processor (optional — auto-installed)
try:
    from dashboard.phone_mic import PhoneMicProcessor
    _PHONE_MIC_AVAILABLE = True
except ImportError:
    _PHONE_MIC_AVAILABLE = False


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


# ---------------------------------------------------------------------------
# Voice Activity Detection (used for Whisper listen loop)
# ---------------------------------------------------------------------------

class _VADBuffer:
    """Enhanced VAD: energy + spectral centroid for robust speech detection."""

    def __init__(
        self,
        sample_rate:    int   = 16_000,
        silence_sec:    float = 1.5,    # silence after last word → send to STT
        speech_thresh:  float = 0.001,  # RMS above this = speech (very sensitive for MacBook mic)
        silence_thresh: float = 0.0005, # RMS below this = silence (very sensitive)
        min_speech_sec: float = 0.3,
        max_speech_sec: float = 30.0,
        centroid_thresh: float = 500.0,  # spectral centroid above this = voice
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
        # Common Whisper misrecognitions of "Jarvis":
        "travis", "trevis", "tarvis", "jarvist", "jarvas",
        "jervis", "jervys", "jerviss", "james", "jams",
        "jardim", "jardin", "jarvin", "jarvins",
        "jervis", "gerver", "jervi",
    ]

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

        # Continuous mode: listen without wake word
        self._continuous_mode = self._config.get("continuous_mode", False)

        # Persistent conversation storage
        from memory.conversation_db import init_db, create_conversation
        init_db()
        self._conv_id = create_conversation()

        # Encrypted dashboard server (lazy-initialized)
        self._dashboard_server = None
        self._phone_mic_processor = None

        self.ui.on_text_command = self._on_text_command

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
            if not word:
                continue
            score = max(
                difflib.SequenceMatcher(None, word, v).ratio()
                for v in self.WAKE_WORD_VARIANTS
            )
            if score >= 0.8:
                # Rebuild the command from the original-cased words after the match.
                command = " ".join(text.split()[i + 1:]).strip()
                if command and command[0] in ",.!?;:":
                    command = command[1:].strip()
                return True, command

        return False, ""

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        # ── ORDER MATTERS for Ollama KV prefix caching ─────────────────────
        # Ollama caches the KV attention state of any stable prompt prefix.
        # By putting the STATIC JARVIS protocol text FIRST, Ollama reuses its
        # cached KV for all those tokens on every request.  Only the small
        # dynamic tail (memory + time, ~50-80 tokens) needs re-evaluation.
        # This turns a 17-second first-token into a sub-second one after warmup.
        #
        # Rule: static content first → semi-static memory middle → dynamic time LAST.
        sys_p   = _load_system_prompt()               # static — never changes mid-session
        memory  = load_memory()
        mem_str = format_memory_for_prompt(memory)    # semi-static — changes only when user tells facts
        now     = datetime.now()
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {now.strftime('%A, %B %d, %Y — %I:%M %p')}\n"
            f"Use this to calculate exact times for reminders."
        )
        parts = [sys_p]
        if mem_str:
            parts.append(mem_str)

        # Location context
        try:
            from actions.location import get_context_string
            loc_ctx = get_context_string()
            if loc_ctx:
                parts.append(f"[LOCATION]\n{loc_ctx}")
        except Exception:
            pass

        # Learned preferences
        try:
            from memory.preferences import format_preferences_for_prompt
            prefs_ctx = format_preferences_for_prompt()
            if prefs_ctx:
                parts.append(prefs_ctx)
        except Exception:
            pass

        # Proactive suggestions
        try:
            from memory.suggestions import format_suggestions_for_prompt
            from actions.location import get_location
            loc = get_location()
            suggestions_ctx = format_suggestions_for_prompt({"location": loc})
            if suggestions_ctx:
                parts.append(suggestions_ctx)
        except Exception:
            pass

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
            text = self._tts_queue.get()
            try:
                if text and self._tts:
                    with self._speaking_lock:
                        self._speaking = True
                    self.ui.set_state("SPEAKING")
                    print(f"[TTS] Speaking: {text[:50]}...")
                    self._tts.speak(text)
                    print(f"[TTS] Done: {text[:50]}...")
                else:
                    print(f"[TTS] Skipped: tts={self._tts is not None}, text={bool(text)}")
            except Exception as e:
                print(f"[TTS] speak error: {e}")
                import traceback; traceback.print_exc()
            finally:
                self._tts_queue.task_done()
                if self._tts_queue.empty():
                    with self._speaking_lock:
                        self._speaking = False
                    if not self.ui.muted:
                        self.ui.set_state("LISTENING")

    def set_speaking(self, value: bool) -> None:
        with self._speaking_lock:
            self._speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def speak(self, text: str) -> None:
        if not text or not self._tts:
            return
        with self._speaking_lock:
            self._speaking = True
        self._tts_queue.put(text)

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
        self._text_queue.put(text)

    # ------------------------------------------------------------------
    # Proactive scheduler
    # ------------------------------------------------------------------

    def _start_proactive_scheduler(self) -> None:
        """Start background scheduler for proactive checks."""
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.interval import IntervalTrigger

            self._scheduler = BackgroundScheduler(daemon=True)
            
            # Check calendar for upcoming meetings every 5 minutes
            self._scheduler.add_job(
                self._proactive_calendar_check,
                IntervalTrigger(minutes=5),
                id="calendar_check",
                replace_existing=True,
            )
            
            # Check system health every 10 minutes
            self._scheduler.add_job(
                self._proactive_system_check,
                IntervalTrigger(minutes=10),
                id="system_check",
                replace_existing=True,
            )
            
            # Daily memory distillation at 3 AM
            self._scheduler.add_job(
                self._proactive_daily_distillation,
                trigger="cron",
                hour=3,
                minute=0,
                id="daily_distillation",
                replace_existing=True,
            )

            self._scheduler.start()
            self.ui.write_log("SYS: Proactive scheduler started")
        except Exception as e:
            self.ui.write_log(f"WARN: Proactive scheduler failed: {e}")

    def _proactive_calendar_check(self) -> None:
        """Check for upcoming calendar events and notify."""
        try:
            from actions.calendar_tool import calendar_tool
            from datetime import datetime, timedelta
            
            now = datetime.now()
            in_30min = now + timedelta(minutes=30)
            
            result = calendar_tool(parameters={
                "action": "list",
                "start": now.isoformat(),
                "end": in_30min.isoformat(),
            }, player=self.ui)
            
            if result and "no events" not in result.lower():
                self.ui.write_log(f"PROACTIVE: Upcoming events: {result[:100]}")
                if not self.ui.muted and not self._speaking:
                    self.speak(f"Sir, you have an upcoming event: {result[:200]}")
        except Exception as e:
            self.ui.write_log(f"Proactive calendar check failed: {e}")

    def _proactive_system_check(self) -> None:
        """Check system health and notify on issues."""
        try:
            import psutil
            
            # Check CPU
            cpu = psutil.cpu_percent(interval=1)
            if cpu > 90:
                self.ui.write_log(f"PROACTIVE: High CPU usage: {cpu}%")
                if not self.ui.muted:
                    self.speak(f"Warning: CPU usage at {cpu} percent")
            
            # Check memory
            mem = psutil.virtual_memory()
            if mem.percent > 90:
                self.ui.write_log(f"PROACTIVE: High memory usage: {mem.percent}%")
                if not self.ui.muted:
                    self.speak(f"Warning: Memory usage at {mem.percent} percent")
            
            # Check disk
            disk = psutil.disk_usage('/')
            if disk.percent > 95:
                self.ui.write_log(f"PROACTIVE: Disk almost full: {disk.percent}%")
                if not self.ui.muted:
                    self.speak("Warning: Disk space critically low")
                    
        except Exception as e:
            self.ui.write_log(f"Proactive system check failed: {e}")

    def _proactive_daily_distillation(self) -> None:
        """Run daily pattern distillation using ReasoningBank."""
        try:
            if not self._config.get("distillation_enabled", False):
                return
            
            self.ui.write_log("SYS: Running daily distillation...")
            
            # Get recent conversations
            from memory.conversation_db import get_messages
            recent = get_messages(self._conv_id, limit=200)
            
            if len(recent) < 10:
                return
            
            # Build conversation summary
            conv_text = "\n".join([
                f"{m['role']}: {m['content'][:500]}" for m in recent
            ])
            
            # Use LLM to extract patterns
            from core.llm_client import call_llm_text
            system = (
                "You are JARVIS analyzing conversation patterns. "
                "Extract: 1) Recurring topics 2) User preferences 3) Frequent requests 4) New facts learned. "
                "Output as JSON with keys: topics, preferences, requests, facts."
            )
            prompt = f"Analyze these conversations:\n{conv_text[:8000]}"
            
            analysis = call_llm_text(prompt, system=system)
            
            # Store distilled patterns
            from memory.memory_manager import update_memory
            try:
                import json
                patterns = json.loads(analysis)
                if isinstance(patterns, dict):
                    for cat, items in patterns.items():
                        if isinstance(items, list):
                            for i, item in enumerate(items):
                                update_memory({cat: {f"distilled_{cat}_{i}": {"value": str(item)}}})
                    self.ui.write_log(f"SYS: Distilled {len(patterns)} pattern categories")
            except Exception:
                # Store raw analysis
                update_memory({"notes": {"distillation_latest": {"value": analysis[:500]}}})
                
        except Exception as e:
            self.ui.write_log(f"Daily distillation failed: {e}")

    # ------------------------------------------------------------------
    # New tool handlers
    # ------------------------------------------------------------------

    def _enroll_voice(self, duration: int = 10) -> str:
        """Enroll user voice for speaker verification."""
        try:
            from core.diarization import enroll_user_from_audio
            import sounddevice as sd
            import numpy as np
            
            self.ui.write_log(f"SYS: Recording voice sample for {duration}s...")
            self.speak(f"Recording voice sample for {duration} seconds. Please speak naturally.")
            
            # Record audio
            sample_rate = 16000
            audio = sd.rec(int(duration * sample_rate), samplerate=sample_rate, channels=1, dtype='float32')
            sd.wait()
            audio = audio.flatten()
            
            if enroll_user_from_audio(audio, sample_rate):
                self.ui.write_log("SYS: Voice enrollment successful")
                self.speak("Voice enrollment complete. I will now recognize your voice.")
                return "Voice enrollment successful."
            else:
                self.ui.write_log("ERR: Voice enrollment failed")
                self.speak("Voice enrollment failed. Please try again.")
                return "Voice enrollment failed."
                
        except Exception as e:
            self.ui.write_log(f"ERR: Voice enrollment error: {e}")
            return f"Voice enrollment error: {e}"

    def _vector_search(self, query: str, category: str | None = None, limit: int = 5) -> str:
        """Semantic search over vector memories."""
        try:
            from memory.vector_memory import search_memory, is_available
            
            if not is_available():
                return "Vector memory not available. Enable vector_memory_enabled in config."
            
            results = search_memory(query, category=category, limit=limit)
            
            if not results:
                return "No relevant memories found."
            
            lines = [f"Found {len(results)} relevant memories:"]
            for r in results:
                lines.append(f"  [{r['category']}/{r['key']}] (score: {r['score']:.2f}) {r['content'][:200]}")
            
            return "\n".join(lines)
            
        except Exception as e:
            return f"Vector search error: {e}"

    def _trigger_distillation(self) -> str:
        """Manually trigger pattern distillation."""
        try:
            self._proactive_daily_distillation()
            return "Distillation triggered. Check logs for results."
        except Exception as e:
            return f"Distillation error: {e}"

    # ------------------------------------------------------------------
    # Encrypted dashboard + remote phone control
    # ------------------------------------------------------------------

    def _remote_control(self, action: str) -> str:
        """Manage the encrypted web dashboard for remote phone control."""
        if not _DASHBOARD_AVAILABLE:
            return ("Encrypted dashboard not available. "
                    "Install deps: pip install fastapi 'uvicorn[standard]' cryptography")

        action = action.lower().strip()

        if action == "start":
            if self._dashboard_server is not None:
                return f"Dashboard already running at {self._dashboard_server.get_url()}"
            try:
                self._dashboard_server = DashboardServer()
                self._dashboard_server.set_wake_callback(
                    lambda: self._text_queue.put("remote wake")
                )

                # Start server in background thread
                import asyncio as _asyncio
                def _run_server():
                    _asyncio.run(self._dashboard_server.serve())
                threading.Thread(target=_run_server, daemon=True).start()

                url = self._dashboard_server.get_url()
                self.ui.write_log(f"SYS: Dashboard running at {url}")
                return f"Encrypted dashboard started at {url}. Press 'Remote Control' or say 'generate QR code' to pair your phone."
            except Exception as e:
                self.ui.write_log(f"ERR: Dashboard start failed: {e}")
                return f"Dashboard start failed: {e}"

        elif action == "stop":
            if self._dashboard_server is None:
                return "Dashboard is not running."
            self._dashboard_server = None
            self.ui.write_log("SYS: Dashboard stopped.")
            return "Dashboard stopped."

        elif action == "status":
            if self._dashboard_server is None:
                return "Dashboard is not running. Say 'start remote control' to launch it."
            url = self._dashboard_server.get_url()
            return f"Dashboard is running at {url}"

        elif action == "url":
            if self._dashboard_server is None:
                return "Dashboard is not running."
            return f"Dashboard URL: {self._dashboard_server.get_url()}"

        elif action == "qr":
            if self._dashboard_server is None:
                # Auto-start
                return self._remote_control("start")
            key = self._dashboard_server.new_key()
            url = self._dashboard_server.get_url()
            qr_url = f"{url}/auto-login?key={key}"
            svg = self._dashboard_server.get_qr_svg(key)
            if svg:
                # Save QR as SVG file for display
                qr_path = BASE_DIR / "dashboard" / "static" / "current_qr.svg"
                qr_path.write_text(svg, encoding="utf-8")
                self.ui.write_log(f"SYS: QR code saved to {qr_path}")
                return (f"QR code generated! Open {url} on your phone and scan, "
                        f"or go to: {qr_url}\nPairing key: {key}")
            else:
                return (f"No QR library (pip install pyqrcode[pil]). "
                        f"Go to: {qr_url}\nPairing key: {key}")

        elif action == "new_key":
            if self._dashboard_server is None:
                return self._remote_control("start")
            key = self._dashboard_server.new_key()
            return f"New pairing key: {key}"

        return f"Unknown remote_control action: {action}. Use: start, stop, status, url, qr, new_key"

    def _phone_mic_action(self, action: str) -> str:
        """Check or control phone microphone streaming status."""
        if self._dashboard_server is None:
            return "Dashboard not running. Start it first with remote_control."

        action = action.lower().strip()

        if action == "status":
            if self._phone_mic_processor and self._phone_mic_processor.is_active():
                stats = self._phone_mic_processor.get_stats()
                return (f"Phone mic active. {stats['frames_processed']} frames processed, "
                        f"{stats['utterances_transcribed']} utterances transcribed. "
                        f"Queue: {stats['queue_size']}, In speech: {stats['in_speech']}")
            qsize = self._dashboard_server._phone_audio_queue.qsize()
            if qsize > 0:
                return f"Phone mic is active. {qsize} audio frames buffered."
            else:
                return "Phone mic is not active. Open the dashboard on your phone and tap the mic button."

        elif action == "stop":
            if self._phone_mic_processor:
                self._phone_mic_processor.stop()
            # Clear the queue
            while not self._dashboard_server._phone_audio_queue.empty():
                try:
                    self._dashboard_server._phone_audio_queue.get_nowait()
                except Exception:
                    break
            return "Phone mic stopped."

        elif action == "queue_size":
            qsize = self._dashboard_server._phone_audio_queue.qsize()
            return f"Phone audio queue: {qsize} frames"

        return f"Unknown phone_mic action: {action}. Use: status, stop, queue_size"

    def _on_phone_transcription(self, text: str) -> None:
        """Callback: receive transcribed text from phone mic and process it."""
        if not text or not text.strip():
            return
        text = text.strip()
        self.ui.write_log(f"PHONE: '{text}'")
        self.ui.add_history("user", f"[Phone] {text}")

        # Feed into the normal message processing pipeline
        self._text_queue.put(text)

    def _verify_voiceprint(self, action: str) -> str:
        """Verify or enroll speaker identity from phone audio."""
        action = action.lower().strip()

        if action == "status":
            try:
                from core.diarization import _load_user_embedding
                emb = _load_user_embedding()
                if emb is not None:
                    return f"Voiceprint enrolled. Embedding shape: {emb.shape}. Voiceprint verification is {'active' if self._config.get('voice_print_enabled') else 'disabled in config'}."
                else:
                    return "No voiceprint enrolled. Say 'enroll voiceprint' to record a voice sample."
            except ImportError:
                return "Voiceprint not available. Install pyannote.audio and speechbrain."
            except Exception as e:
                return f"Voiceprint status error: {e}"

        elif action == "enroll":
            if self._phone_mic_processor is None:
                return "Phone mic not active. Open the dashboard and tap the mic button first."
            # Use the existing enroll_voice tool
            return self._enroll_voice(duration=10)

        elif action == "test":
            if self._phone_mic_processor is None:
                return "Phone mic not active. Open the dashboard and tap the mic button first."
            if not self._phone_mic_processor.is_active():
                return "Phone mic is not streaming. Tap the mic button on the dashboard."
            stats = self._phone_mic_processor.get_stats()
            if stats["utterances_transcribed"] == 0:
                return "No utterances captured yet. Speak into the phone mic."
            return (f"Voiceprint test: {stats['utterances_transcribed']} utterances transcribed. "
                    f"Check logs for speaker identification results.")

        return f"Unknown verify_voiceprint action: {action}. Use: status, enroll, test"

    def _manage_routines(self, args: dict) -> str:
        """Create, list, enable, disable, or delete automation routines."""
        from actions.routines import (
            add_routine, remove_routine, toggle_routine,
            list_routines, get_routine,
        )

        action = args.get("action", "list").lower().strip()

        if action == "list":
            routines = list_routines()
            if not routines:
                return "No routines configured. Create one with action 'create'."
            lines = [f"Found {len(routines)} routine(s):"]
            for r in routines:
                state = "ON" if r.get("enabled", True) else "OFF"
                rtype = r.get("type", "cron")
                cmd = r.get("command", "")[:60]
                lines.append(f"  [{state}] {r['name']} ({rtype}): {cmd}")
            return "\n".join(lines)

        elif action == "create":
            name = args.get("name", "")
            command = args.get("command", "")
            if not name or not command:
                return "Need 'name' and 'command' for create."
            routine_type = args.get("routine_type", "cron")
            schedule = args.get("schedule")
            interval = args.get("interval")
            return add_routine(
                name=name, command=command, routine_type=routine_type,
                schedule=schedule, interval=interval,
                execute_fn=lambda cmd: self._text_queue.put(cmd),
            )

        elif action == "enable":
            rid = args.get("routine_id", "")
            if not rid:
                return "Need 'routine_id' for enable."
            return toggle_routine(rid, enabled=True)

        elif action == "disable":
            rid = args.get("routine_id", "")
            if not rid:
                return "Need 'routine_id' for disable."
            return toggle_routine(rid, enabled=False)

        elif action == "delete":
            rid = args.get("routine_id", "")
            if not rid:
                return "Need 'routine_id' for delete."
            return remove_routine(rid)

        return f"Unknown action: {action}. Use: create, list, enable, disable, delete"

    def _manage_calendar(self, args: dict) -> str:
        """Manage calendar events."""
        from actions.calendar_tool import get_upcoming, add_event, remove_event, list_events

        action = args.get("action", "upcoming").lower().strip()

        if action == "upcoming":
            hours = args.get("hours", 24)
            events = get_upcoming(hours=hours)
            if not events:
                return f"No events in the next {hours} hours."
            lines = [f"Upcoming events ({len(events)}):"]
            for e in events:
                lines.append(f"  {e.get('time', '?')} — {e.get('title', 'Untitled')}")
            return "\n".join(lines)

        elif action == "add":
            title = args.get("title", "")
            time_str = args.get("time", "")
            if not title or not time_str:
                return "Need 'title' and 'time' for add."
            desc = args.get("description", "")
            return add_event(title, time_str, desc)

        elif action == "remove":
            eid = args.get("event_id", "")
            if not eid:
                return "Need 'event_id' for remove."
            return remove_event(eid)

        elif action == "list":
            events = list_events()
            if not events:
                return "No events scheduled."
            lines = [f"Scheduled events ({len(events)}):"]
            for e in events:
                lines.append(f"  {e.get('time', '?')} — {e.get('title', 'Untitled')}")
            return "\n".join(lines)

        return f"Unknown action: {action}. Use: upcoming, add, remove, list"

    def _set_location(self, args: dict) -> str:
        """Set or get location for context-aware responses."""
        from actions.location import get_location, set_location, get_context_string

        city = args.get("city", "").strip()
        if not city:
            loc = get_location()
            return get_context_string()

        region = args.get("region", "")
        country = args.get("country", "")
        return set_location(city, region, country)

    def _system_status(self) -> str:
        """Show system performance metrics and resource utilization."""
        import psutil
        import os

        proc = psutil.Process(os.getpid())
        mem = proc.memory_info()

        parts = ["=== JARVIS System Status ==="]

        # Memory
        parts.append(f"\nMemory:")
        parts.append(f"  RSS: {mem.rss / (1024*1024):.1f} MB")
        parts.append(f"  VMS: {mem.vms / (1024*1024):.1f} MB")

        # Conversation
        parts.append(f"\nConversation:")
        parts.append(f"  Messages: {len(self._conversation)}")
        parts.append(f"  Conv ID: {self._conv_id}")

        # Dashboard
        if self._dashboard_server:
            parts.append(f"\nDashboard:")
            parts.append(f"  URL: {self._dashboard_server.get_url()}")
            parts.append(f"  Clients: {len(self._dashboard_server._clients)}")
            parts.append(f"  History: {len(self._dashboard_server._history)} msgs")

        # Phone mic
        if self._phone_mic_processor and self._phone_mic_processor.is_active():
            stats = self._phone_mic_processor.get_stats()
            parts.append(f"\nPhone Mic:")
            parts.append(f"  Frames: {stats['frames_processed']}")
            parts.append(f"  Utterances: {stats['utterances_transcribed']}")
            parts.append(f"  In speech: {stats['in_speech']}")

        # Performance metrics
        try:
            from core.performance import perf_monitor, get_llm_cache, get_tool_cache
            parts.append(f"\nPerformance:")
            parts.append(f"  {perf_monitor.report()}")

            llm_stats = get_llm_cache().stats()
            parts.append(f"\nLLM Cache:")
            parts.append(f"  Size: {llm_stats['size']}/{llm_stats['max_size']}")
            parts.append(f"  Hit rate: {llm_stats['hit_rate']}")

            tool_stats = get_tool_cache().stats()
            parts.append(f"\nTool Cache:")
            parts.append(f"  Size: {tool_stats['size']}/{tool_stats['max_size']}")
            parts.append(f"  Hit rate: {tool_stats['hit_rate']}")
        except Exception:
            pass

        # Routines
        try:
            from actions.routines import list_routines
            routines = list_routines()
            active = sum(1 for r in routines if r.get("enabled", True))
            parts.append(f"\nRoutines: {active}/{len(routines)} active")
        except Exception:
            pass

        return "\n".join(parts)

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

            elif name == "enroll_voice":
                r = self._enroll_voice(args.get("duration", 10))
                return r

            elif name == "vector_search":
                r = self._vector_search(args.get("query", ""), args.get("category"), args.get("limit", 5))
                return r

            elif name == "trigger_distillation":
                r = self._trigger_distillation()
                return r

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

            elif name == "manage_routines":
                r = self._manage_routines(args)
                return r

            elif name == "analyze_image":
                from actions.image_processor import process_image_upload
                path = args.get("image_path", "")
                prompt = args.get("prompt")
                if not path and self.ui.current_file:
                    path = self.ui.current_file
                if not path:
                    return "No image path provided."
                r = process_image_upload(path, user_prompt=prompt)
                return r

            elif name == "manage_calendar":
                r = self._manage_calendar(args)
                return r

            elif name == "set_location":
                r = self._set_location(args)
                return r

            elif name == "summarize_conversation":
                from memory.summarizer import summarize_conversation, save_conversation_summary
                summary = summarize_conversation(self._conversation)
                save_conversation_summary(self._conv_id, summary, self._conversation)
                return f"Conversation summary:\n{summary}"

            elif name == "learn_preference":
                from memory.preferences import learn_preference
                cat = args.get("category", "notes")
                key = args.get("key", "")
                val = args.get("value", "")
                if not key or not val:
                    return "Need 'key' and 'value'."
                return learn_preference(cat, key, val)

            elif name == "system_status":
                r = self._system_status()
                return r

            elif name == "remote_control":
                r = self._remote_control(args.get("action", "status"))
                return r

            elif name == "phone_mic":
                r = self._phone_mic_action(args.get("action", "status"))
                return r

            elif name == "verify_voiceprint":
                r = self._verify_voiceprint(args.get("action", "status"))
                return r

            return f"Unknown tool: {name}"

        # shutdown_jarvis bypasses timeout — must run in main thread context
        if name == "shutdown_jarvis":
            self.ui.write_log("SYS: Shutdown requested.")
            
            # Stop proactive scheduler
            if hasattr(self, '_scheduler') and self._scheduler:
                try:
                    self._scheduler.shutdown(wait=False)
                except Exception:
                    pass

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

        # Auto-learn preferences from conversation
        try:
            from memory.preferences import auto_learn_from_conversation
            auto_learn_from_conversation(self._conversation)
        except Exception:
            pass

    def _broadcast_dashboard(self, msg: dict) -> None:
        """Send a message to all connected dashboard clients."""
        if self._dashboard_server is not None:
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(self._dashboard_server.broadcast(msg))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # LLM processing loop
    # ------------------------------------------------------------------

    def _process_message(self, user_text: str) -> None:
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
        self.ui.set_state("THINKING")
        self.ui.write_log(f"You: {user_text}")
        self.ui.add_history("user", user_text)

        self._conversation.append({"role": "user", "content": user_text})

        # Persist to SQLite
        from memory.conversation_db import add_message
        add_message(self._conv_id, "user", user_text)

        MAX_HISTORY = 12
        if len(self._conversation) > MAX_HISTORY:
            try:
                from core.performance import trim_conversation
                self._conversation = trim_conversation(self._conversation, MAX_HISTORY)
            except Exception:
                self._conversation = self._conversation[-MAX_HISTORY:]

        # Mark activity for idle detection
        try:
            from core.performance import idle_detector
            idle_detector.mark_active()
        except Exception:
            pass

        # Include semantically relevant memories from vector DB
        vector_memories = ""
        try:
            from memory.memory_manager import format_vector_memory_for_prompt
            vector_memories = format_vector_memory_for_prompt(user_text, limit=5)
        except Exception:
            pass

        system_prompt = self._build_system_prompt()
        if vector_memories:
            system_prompt += "\n\n" + vector_memories

        messages = [
            {"role": "system", "content": system_prompt}
        ] + list(self._conversation)

        # Tools whose output needs a second LLM round to summarise/interpret.
        # Everything else returns a user-ready string → speak directly.
        _NEEDS_LLM_ROUND = {"web_search", "screen_process", "agent_task"}

        MAX_TOOL_ROUNDS = 6
        for _round in range(MAX_TOOL_ROUNDS):
            final_content    = ""
            final_tool_calls: list = []
            # Sentences already queued to TTS during streaming (may be empty
            # for tool-call rounds where the model emits no content).
            _streamed: list[str] = []

            try:
                for event in call_llm_stream(messages, CORE_TOOLS):
                    if event["type"] == "sentence":
                        # ── Overlap TTS with LLM generation ─────────────────
                        # Queue this sentence immediately; the TTS worker
                        # synthesises it while the LLM is still generating
                        # the next one.
                        _streamed.append(event["text"])
                        self.speak(event["text"])
                        self.ui.stream_sentence(event["text"])
                        # Stream to dashboard in real-time
                        self._broadcast_dashboard({
                            "type": "stream",
                            "text": event["text"],
                        })
                    elif event["type"] == "done":
                        final_content    = event["content"]
                        final_tool_calls = event["tool_calls"]
            except RuntimeError as e:
                self.speak_error("LLM", e)
                return

            # ── No tool calls: pure conversational reply ─────────────────────
            if not final_tool_calls:
                if _streamed:
                    # Sentences already shown in log via stream_sentence — skip full write_log.
                    assistant_msg = {"role": "assistant", "content": final_content}
                    messages.append(assistant_msg)
                    self._conversation.append(assistant_msg)
                    self.ui.add_history("jarvis", final_content)
                elif final_content:
                    # Very short response (no sentence boundary) — speak now.
                    assistant_msg = {"role": "assistant", "content": final_content}
                    messages.append(assistant_msg)
                    self._conversation.append(assistant_msg)
                    self.ui.write_log(f"Jarvis: {final_content}")
                    self.ui.add_history("jarvis", final_content)
                    self._persist_assistant(final_content)
                    self.speak(final_content)
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
                    self.speak(final_content)
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
                self._broadcast_dashboard({
                    "type": "tool_start",
                    "tool": tname,
                    "args": {k: str(v)[:100] for k, v in targs.items()},
                })
                result = self._execute_tool(tname, targs)
                self._broadcast_dashboard({
                    "type": "tool_result",
                    "tool": tname,
                    "result": str(result)[:200],
                })

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
                self.speak(_ack)
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
                self.speak(_reply)
                break

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

    # ------------------------------------------------------------------
    # STT listening loops
    # ------------------------------------------------------------------

    def _listen_whisper(self) -> None:
        """Mic → VAD → Whisper → Wake Word Check → LLM loop."""
        vad = _VADBuffer()
        q: queue.Queue = queue.Queue(maxsize=200)
        
        # Diarization processor for continuous mode
        dia_processor = None
        if self._continuous_mode and _DIARIZATION_READY and diarization_available():
            try:
                dia_processor = DiarizationProcessor(sample_rate=SAMPLE_RATE_IN)
                self.ui.write_log("SYS: Speaker diarization enabled")
            except Exception as e:
                self.ui.write_log(f"WARN: Diarization init failed: {e}")

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                is_speaking = self._speaking
            if not is_speaking and not self.ui.muted:
                try:
                    q.put_nowait(indata.copy())
                except queue.Full:
                    pass

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE_IN,
                channels=CHANNELS,
                dtype="float32",
                blocksize=BLOCK_SIZE,
                callback=callback,
            ):
                mode_label = "Continuous" if self._continuous_mode else "Wake word: JARVIS"
                self.ui.write_log(f"SYS: Mic active (Whisper STT) — {mode_label}")
                while True:
                    try:
                        chunk = q.get(timeout=0.1)
                        audio = vad.process(chunk.flatten())
                        if audio is not None:
                            if self._stt is None:
                                self.ui.set_state("IDLE")
                                continue   # STT failed to load — don't crash the loop
                            self.ui.set_state("THINKING")
                            text = self._stt.transcribe(audio)
                            if text.strip():
                                if self._continuous_mode:
                                    # Continuous mode: check if user is speaking
                                    if dia_processor:
                                        speaker, is_user = dia_processor.process_chunk(audio)
                                        if not is_user:
                                            self.ui.write_log(f"SKIP: '{text}' (speaker: {speaker}, not enrolled user)")
                                            continue
                                        self.ui.write_log(f"USER ({speaker}): '{text}'")
                                    else:
                                        self.ui.write_log(f"USER: '{text}'")
                                    self._process_message(text)
                                else:
                                    # Wake word mode: check for wake word
                                    is_wake, command = self._check_wake_word(text)
                                    if is_wake:
                                        if command:
                                            self.ui.write_log(f"WAKE: '{text}' → Command: '{command}'")
                                            self._process_message(command)
                                        else:
                                            self.ui.write_log(f"WAKE: '{text}' (no command)")
                                            self.speak("Sim?")
                                    else:
                                        self.ui.write_log(f"SKIP: '{text}' (no wake word)")
                    except queue.Empty:
                        pass
        except Exception as e:
            print(f"[STT-Whisper] Mic error: {e}")
            traceback.print_exc()

    def _listen_vosk(self) -> None:
        """Mic → Vosk streaming → Wake Word Check → LLM loop."""
        q: queue.Queue = queue.Queue(maxsize=200)

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                is_speaking = self._speaking
            if not is_speaking and not self.ui.muted:
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
                            if self._continuous_mode:
                                self.ui.write_log(f"USER: '{text}'")
                                self._process_message(text)
                            else:
                                is_wake, command = self._check_wake_word(text)
                                if is_wake:
                                    if command:
                                        self.ui.write_log(f"WAKE: '{text}' → Command: '{command}'")
                                        self._process_message(command)
                                    else:
                                        self.ui.write_log(f"WAKE: '{text}' (no command)")
                                        self.speak("Sim?")
                                else:
                                    self.ui.write_log(f"SKIP: '{text}' (no wake word)")
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
            from core.llm_client import ensure_ollama_running, warmup_model
            self.ui.write_log("SYS: Checking Ollama…")
            if ensure_ollama_running():
                self.ui.write_log("SYS: Ollama OK.")
            else:
                self.ui.write_log("ERR: Ollama unavailable — run: ollama serve")

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
            self.ui.write_log("SYS: JARVIS online.")
            self.ui.set_state("LISTENING")
            self.ui.set_startup_status("● JARVIS online · Voice loading in background…")

            # Start encrypted dashboard (replaces basic Flask dashboard)
            try:
                if _DASHBOARD_AVAILABLE:
                    self._dashboard_server = DashboardServer()
                    self._dashboard_server.set_wake_callback(
                        lambda: self._text_queue.put("remote wake")
                    )

                    import asyncio as _asyncio
                    def _run_dashboard():
                        _asyncio.run(self._dashboard_server.serve())
                    threading.Thread(target=_run_dashboard, daemon=True).start()

                    dash_url = self._dashboard_server.get_url()
                    self.ui.write_log(f"SYS: Encrypted dashboard at {dash_url}")

                    # Start phone mic processor (transcribes phone audio via Whisper)
                    if _PHONE_MIC_AVAILABLE and self._stt is not None:
                        import dashboard.server as _ds_module
                        _vp_enabled = self._config.get("voice_print_enabled", False)
                        self._phone_mic_processor = PhoneMicProcessor(
                            stt_engine=self._stt,
                            on_transcription=self._on_phone_transcription,
                            on_partial=lambda text: self.ui.write_log(f"PHONE (partial): {text}"),
                            on_status=lambda status: self.ui.write_log(f"SYS: {status}"),
                            broadcast_fn=self._dashboard_server.broadcast,
                            voiceprint_enabled=_vp_enabled,
                        )
                        self._phone_mic_processor.start()
                        # Wire into dashboard server
                        _ds_module._phone_mic_processor = self._phone_mic_processor
                        self.ui.write_log(f"SYS: Phone mic → Whisper transcription active"
                                          f"{' (voiceprint enabled)' if _vp_enabled else ''}")
                else:
                    # Fallback to basic Flask dashboard
                    from web.dashboard import start_web_dashboard
                    start_web_dashboard()
                    self.ui.write_log("SYS: Basic web dashboard at http://localhost:5050")
            except Exception as e:
                self.ui.write_log(f"ERR: Web dashboard — {e}")

            # Migrate to vector memory if enabled
            if self._config.get("vector_memory_enabled", False):
                try:
                    from memory.memory_manager import migrate_to_vector_db
                    count = migrate_to_vector_db()
                    if count:
                        self.ui.write_log(f"SYS: Migrated {count} memories to vector DB")
                except Exception as e:
                    self.ui.write_log(f"WARN: Vector memory migration failed: {e}")

            # Start proactive scheduler
            self._start_proactive_scheduler()

            # Initialize automation routines
            try:
                from actions.routines import init_routines
                init_routines(lambda cmd: self._text_queue.put(cmd))
                self.ui.write_log("SYS: Automation routines loaded")
            except Exception as e:
                self.ui.write_log(f"WARN: Routines init failed: {e}")

            threading.Thread(target=self._tts_worker,        daemon=True).start()
            threading.Thread(target=self._text_command_loop,  daemon=True).start()

            # STT loop — blocks this thread forever
            if stt_engine == "vosk":
                self._listen_vosk()
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
