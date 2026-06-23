# 🤖 MARK XL — Local AI Assistant

> **J.A.R.V.I.S** — Just A Rather Very Intelligent System
> Cross-platform voice AI assistant running entirely on local hardware. No cloud APIs required.

---

## Overview

MARK XL is a fully local, real-time voice and visual AI agent. It combines offline speech recognition, a locally hosted LLM (via Ollama), and text-to-speech to deliver a privacy-first personal assistant with OS-level control capabilities.

Successor to the previous mark, which used the Google Gemini Live API. MARK XL removes all cloud LLM dependencies while adding streaming responses, a dynamic configuration UI, and multi-language support.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      MARK XL — JARVIS                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐              │
│  │ Microphone│───▶│ STT      │───▶│ LLM      │              │
│  │ (Vosk/   │    │ (Whisper)│    │ (Ollama) │              │
│  │ Whisper) │    └──────────┘    └────┬─────┘              │
│  └──────────┘                        │                     │
│                                      ▼                     │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐              │
│  │ Dashboard │◀───│ Broadcast│◀───│ Tool     │              │
│  │ (FastAPI) │    │ System   │    │ Router   │              │
│  └──────────┘    └──────────┘    └────┬─────┘              │
│       ▲                               │                     │
│       │                               ▼                     │
│  ┌──────────┐                   ┌──────────┐              │
│  │ Phone    │                   │ 40+ Tools│              │
│  │ (QR +    │                   │ (OS, web,│              │
│  │  AES256) │                   │  files…) │              │
│  └──────────┘                   └──────────┘              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Core Components

| Layer | Technology | Notes |
|-------|-----------|-------|
| **STT** | faster-whisper / Vosk | Fully offline. Auto language detection or forced locale. |
| **LLM** | Ollama (any model) | qwen2.5, llama3.2, mistral, etc. Streaming + tool calling. |
| **TTS** | EdgeTTS / Kokoro / ElevenLabs | EdgeTTS = free + internet. Kokoro = fully offline. |
| **UI** | PyQt6 | HUD overlay with system monitor, log panel, file drop zone. |
| **Dashboard** | FastAPI + WebSocket + AES-256 | Encrypted remote control from phone. QR pairing. |
| **Agent** | Custom task queue | Multi-step planner + executor + error recovery. |

---

## Features

### Core
- **Streaming responses** — TTS starts speaking on the first sentence, not after the full response
- **Tool calling** — 40+ built-in tools: browser control, file management, weather, YouTube, messaging, screen analysis, code helper, game updater, flight finder, and more
- **Long-term memory** — Silently saves personal facts; recalled in every conversation
- **Live configuration** — Change LLM model, STT engine, TTS voice without restarting (⚙ Configure button)
- **Ollama auto-start** — Automatically launches `ollama serve` if it's not running
- **Model warmup** — Pre-loads the LLM into memory during startup
- **Multi-language STT** — Set `stt_language` to `auto` (Whisper detects) or a specific locale (`tr`, `de`, `fr`, …)
- **Cross-platform** — Windows, macOS, Linux (OS detected automatically at runtime)
- **File drop zone** — Drag and drop images, PDFs, Word docs, CSV, audio, video for AI processing

### Remote Phone Control
- **Encrypted Dashboard** — FastAPI + WebSocket + AES-256-CBC client-side encryption
- **QR Code Pairing** — Scan QR code to instantly connect your phone
- **Phone Microphone Streaming** — PCM16 audio from phone → local Whisper transcription
- **Real-time Chat** — Encrypted bidirectional messaging via WebSocket
- **File Sharing** — Upload/download files between phone and computer
- **Voice Commands** — Speak commands from your phone, JARVIS processes them locally
- **Voiceprint Verification** — Identify speakers from phone audio (pyannote + speechbrain)
- **Device Persistence** — Known devices auto-reconnect without re-pairing

### Smart Home
- **TP-Link Kasa** — Auto-discover, control, energy monitoring, brightness/color control
- **Routine Automation** — Cron-based, interval, and one-shot timer routines
- **Scheduled Actions** — "Turn on lights at 7pm every weekday"

### Contextual Intelligence
- **Multi-modal Input** — Analyze images via vision-capable LLM (Ollama llava)
- **Calendar Management** — Add, list, remove, and check upcoming events
- **Location Awareness** — IP-based geolocation, timezone, local time context
- **Proactive Suggestions** — Time/context-aware recommendations

### Memory & Learning
- **Conversation Summarization** — LLM-based summarization of long conversations
- **Preference Learning** — Auto-extract user preferences from conversation
- **Vector Memory** — Semantic search over stored memories (AgentDB)
- **Pattern Distillation** — Extract recurring topics and learned facts
- **Speaker Diarization** — Identify different speakers in continuous mode

### Performance
- **Response Caching** — LRU cache with TTL for LLM and tool responses
- **Lazy Module Loading** — Reduce startup time by deferring imports
- **Conversation Trimming** — Memory-aware pruning with orphan cleanup
- **Idle Detection** — Reduce resource usage when inactive
- **Performance Monitoring** — Latency tracking, cache hit rates, memory stats

---

## Requirements

- Python 3.11 or 3.12
- [Ollama](https://ollama.com) installed and a model pulled (e.g. `ollama pull qwen2.5:7b`)
- A microphone

### Optional Dependencies

| Feature | Package | Install |
|---------|---------|---------|
| Encrypted Dashboard | fastapi, uvicorn, cryptography | Auto-installed |
| QR Code Pairing | pyqrcode[pil] | Auto-installed |
| Voiceprint | pyannote.audio, speechbrain | `config: voice_print_enabled` |
| Vector Memory | agentdb | `config: vector_memory_enabled` |
| Image Analysis | Ollama llava model | `ollama pull llava` |
| Smart Home | python-kasa | `pip install python-kasa` |

---

## Quick Start

```bash
# 1. Install Ollama → https://ollama.com
#    Then pull a model:
ollama pull qwen2.5:7b

# 2. Clone / download the project and launch
cd Jarvis-Mark-XL
python main.py
```

That's it. On first run MARK XL:
1. Auto-installs base packages (PyQt6, numpy …) and restarts once
2. Opens the **Initialisation** overlay — choose STT engine, LLM model, TTS engine
3. Click **INITIALISE SYSTEMS** — engine packages install in the background (progress shown in log)
4. JARVIS comes online

After setup, use the **⚙ CONFIGURE** button in the right panel to change any setting at any time without restarting.

### Remote Phone Control

```bash
# 1. Start JARVIS (dashboard auto-starts on port 8000)
python main.py

# 2. Say "generate QR code" or "start remote control"
# 3. Scan the QR code with your phone
# 4. Your phone is now connected!
```

---

## Configuration (`config/api_keys.json`)

```json
{
    "stt_engine":         "whisper",
    "stt_model":          "base",
    "stt_language":       "auto",
    "llm_url":            "http://localhost:11434",
    "llm_model":          "qwen2.5:7b",
    "tts_engine":         "edgetts",
    "tts_voice":          "en-US-GuyNeural",
    "elevenlabs_api_key": "",

    "vector_memory_enabled":   false,
    "diarization_enabled":     false,
    "voice_print_enabled":     false,
    "distillation_enabled":    false,
    "vision_model":            "llava"
}
```

| Key | Values | Default |
|-----|--------|---------|
| `stt_engine` | `whisper` / `vosk` | `whisper` |
| `stt_model` | `tiny` / `base` / `small` / `medium` / `large-v3` | `base` |
| `stt_language` | `auto` or ISO code (`tr`, `en`, `de` …) | `auto` |
| `llm_url` | Ollama API base URL | `http://localhost:11434` |
| `llm_model` | Any model pulled in Ollama | `qwen2.5:7b` |
| `tts_engine` | `edgetts` / `kokoro` / `elevenlabs` | `edgetts` |
| `tts_voice` | Voice name / ID depending on engine | `en-US-GuyNeural` |
| `vision_model` | Ollama vision model | `llava` |

---

## Built-in Tools (40+)

### Core Tools
| Tool | Description |
|------|-------------|
| `open_app` | Opens any application or website |
| `web_search` | Web search and compare mode |
| `weather_report` | Current weather for any city |
| `send_message` | WhatsApp / Telegram messaging |
| `reminder` | Timed reminders via Task Scheduler |
| `youtube_video` | Play, summarize, trending videos |
| `screen_process` | Screen capture + vision model analysis |
| `computer_settings` | Volume, brightness, window management, shortcuts |
| `browser_control` | Full Playwright browser automation |
| `file_controller` | File/folder CRUD, search, disk usage |
| `desktop_control` | Wallpaper, organize, clean desktop |
| `code_helper` | Write, edit, explain, run code |
| `dev_agent` | Build complete multi-file projects |
| `agent_task` | Multi-step autonomous task execution |
| `computer_control` | Direct mouse/keyboard control |
| `game_updater` | Steam / Epic Games install & update |
| `flight_finder` | Google Flights search |
| `file_processor` | Process images, PDFs, CSV, audio, video |

### Remote Control Tools
| Tool | Description |
|------|-------------|
| `remote_control` | Start/stop/status/qr/url for encrypted dashboard |
| `phone_mic` | Phone microphone streaming status/control |
| `verify_voiceprint` | Enroll/test speaker identity from phone |

### Smart Home Tools
| Tool | Description |
|------|-------------|
| `smart_home` | TP-Link Kasa control (discover, power, brightness, color, energy) |
| `manage_routines` | Create/list/enable/disable/delete automation routines |

### Intelligence Tools
| Tool | Description |
|------|-------------|
| `analyze_image` | Describe images, OCR, answer questions via vision LLM |
| `manage_calendar` | Add/list/remove/upcoming calendar events |
| `set_location` | Set/get location for context-aware responses |
| `summarize_conversation` | Auto-summarize long conversations |
| `learn_preference` | Explicitly store user preferences |
| `system_status` | Performance metrics, cache stats, memory usage |

---

## Voice Commands

| Command | Action |
|---------|--------|
| "Jarvis, what's the weather?" | Triggers weather_report tool |
| "Open WhatsApp" | Launches WhatsApp |
| "Start remote control" | Starts encrypted dashboard |
| "Generate QR code" | Creates phone pairing QR |
| "Discover smart home devices" | Scans network for Kasa devices |
| "Set a routine to turn on lights at 7pm" | Creates cron routine |
| "Summarize our conversation" | Creates conversation summary |
| "Where am I?" | Shows location context |
| "System status" | Shows performance metrics |

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `F4` | Mute / unmute microphone |
| `F11` | Toggle fullscreen |

---

## TTS Engine Comparison

| Engine | Internet | Quality | Cost |
|--------|----------|---------|------|
| EdgeTTS | Required | Good | Free |
| Kokoro | No | Excellent | Free (local model ~100 MB) |
| ElevenLabs | Required | Best | Paid API |

---

## Project Structure

```
Jarvis-Mark-XL/
├── main.py                    # Main entry point + JarvisLocal orchestrator
├── core/
│   ├── tools.py               # Canonical tool schema (40+ tools)
│   ├── installer.py           # Auto-dependency installer
│   ├── llm_client.py          # Ollama LLM client (streaming + tool calling)
│   ├── stt.py                 # Whisper/Vosk STT engines
│   ├── tts.py                 # EdgeTTS/Kokoro/ElevenLabs TTS
│   ├── diarization.py         # Speaker diarization + voiceprint
│   ├── performance.py         # Response cache, idle detection, perf monitor
│   ├── error_handling.py      # Retry, safe_execute, graceful degradation
│   └── paths.py               # Path constants
├── dashboard/
│   ├── server.py              # FastAPI + WebSocket + AES-256 encrypted server
│   ├── phone_mic.py           # Phone mic processor (PCM16 → VAD → Whisper)
│   └── static/
│       ├── login.html         # PIN entry with QR pairing
│       └── app.html           # Dashboard chat, mic streaming, file upload
├── actions/
│   ├── routines.py            # Automation routine engine
│   ├── calendar_tool.py       # Calendar event management
│   ├── location.py            # Location awareness + geolocation
│   ├── image_processor.py     # Multi-modal image analysis
│   ├── kasa_tool.py           # TP-Link Kasa smart home
│   └── ...                    # Other action modules
├── memory/
│   ├── memory_manager.py      # Long-term memory management
│   ├── vector_memory.py       # AgentDB vector memory
│   ├── conversation_db.py     # SQLite conversation persistence
│   ├── summarizer.py          # Conversation summarization
│   ├── preferences.py         # Preference learning
│   └── suggestions.py         # Proactive suggestions
├── ui/
│   ├── main_window.py         # PyQt6 HUD + SetupOverlay
│   └── widgets.py             # Reusable UI components
├── web/
│   └── dashboard.py           # Basic Flask dashboard (fallback)
├── config/
│   ├── api_keys.json          # Runtime configuration
│   └── routines.json          # Automation routines
└── memory/
    ├── facts.json             # Long-term memory facts
    ├── user_embedding.npy     # Voiceprint enrollment
    └── conversation_summaries.json
```

---

## License

MIT — FatihMakes Industries
