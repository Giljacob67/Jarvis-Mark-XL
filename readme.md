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
Microphone → STT (Whisper / Vosk)
                  ↓
           Ollama LLM (tool calling + streaming)
                  ↓
         Tool Execution (OS, Browser, Files …)
                  ↓
           TTS (EdgeTTS / Kokoro / ElevenLabs)
                  ↓
              Speaker
```

### Core Components

| Layer | Technology | Notes |
|-------|-----------|-------|
| **STT** | faster-whisper / Vosk | Fully offline. Auto language detection or forced locale. |
| **LLM** | Ollama (any model) | qwen2.5, llama3.2, mistral, etc. Streaming + tool calling. |
| **TTS** | EdgeTTS / Kokoro / ElevenLabs | EdgeTTS = free + internet. Kokoro = fully offline. |
| **UI** | PyQt6 | HUD overlay with system monitor, log panel, file drop zone. |
| **Agent** | Custom task queue | Multi-step planner + executor + error recovery. |

---

## Features

- **Streaming responses** — TTS starts speaking on the first sentence, not after the full response
- **Tool calling** — 18 built-in tools: browser control, file management, weather, YouTube, messaging, screen analysis, code helper, game updater, flight finder, and more
- **Long-term memory** — Silently saves personal facts; recalled in every conversation
- **Live configuration** — Change LLM model, STT engine, TTS voice without restarting (⚙ Configure button)
- **Ollama auto-start** — Automatically launches `ollama serve` if it's not running
- **Model warmup** — Pre-loads the LLM into memory during startup so the first message is as fast as subsequent ones
- **Multi-language STT** — Set `stt_language` to `auto` (Whisper detects) or a specific locale (`tr`, `de`, `fr`, …)
- **Cross-platform** — Windows, macOS, Linux (OS detected automatically at runtime)
- **File drop zone** — Drag and drop images, PDFs, Word docs, CSV, audio, video for AI processing

---

## Requirements

- Python 3.11 or 3.12
- [Ollama](https://ollama.com) installed and a model pulled (e.g. `ollama pull qwen2.5:7b`)
- A microphone

---

## Quick Start

```bash
# 1. Install Ollama → https://ollama.com
#    Then pull a model:
ollama pull qwen2.5:7b

# 2. Clone / download the project and launch
cd Mark-XL
python main.py
```

That's it. On first run MARK XL:
1. Auto-installs base packages (PyQt6, numpy …) and restarts once
2. Opens the **Initialisation** overlay — choose STT engine, LLM model, TTS engine
3. Click **INITIALISE SYSTEMS** — engine packages install in the background (progress shown in log)
4. JARVIS comes online

After setup, use the **⚙ CONFIGURE** button in the right panel to change any setting at any time without restarting.

---

## Configuration (`config/api_keys.json`)

```json
{
    "stt_engine":         "whisper",
    "stt_model":          "small",
    "stt_language":       "pt",
    "llm_provider":       "ollama_cloud",
    "llm_url":            "https://ollama.com",
    "llm_model":          "gpt-oss:120b-cloud",
    "ollama_api_key":     "YOUR_OLLAMA_CLOUD_KEY",
    "vision_model":       "llava:7b",
    "tts_engine":         "elevenlabs",
    "tts_voice":          "pNInz6obpgDQGcFmaJgB",
    "elevenlabs_api_key": "YOUR_ELEVENLABS_KEY",
    "allow_code_execution": false
}
```

| Key | Values | Default |
|-----|--------|---------|
| `stt_engine` | `whisper` / `vosk` | `whisper` |
| `stt_model` | `tiny` / `base` / `small` / `medium` / `large-v3` | `base` |
| `stt_language` | `auto` or ISO code (`tr`, `en`, `de` …) | `auto` |
| `llm_provider` | `ollama` / `ollama_cloud` / `openai` | `ollama_cloud` (first boot) |
| `llm_url` | API base URL | `https://ollama.com` (cloud) |
| `llm_model` | Model name | `gpt-oss:120b-cloud` |
| `ollama_api_key` | Ollama Cloud bearer token | — |
| `tts_engine` | `edgetts` / `kokoro` / `elevenlabs` | `elevenlabs` (first boot) |
| `tts_voice` | Voice name / ID depending on engine | `pt-BR-AntonioNeural` |
| `voice_barge_in` | `true` / `false` — interrupt speech by saying the wake word while Jarvis is talking (best with headphones or a mic with echo cancellation) | `false` |
| `tts_fallback_voice` | EdgeTTS voice used when the primary TTS engine fails mid-response | `pt-BR-AntonioNeural` |
| `proactive_enabled` | `true` / `false` — spontaneous speech engine (morning briefing, unread email, event reminders) | `false` |
| `proactive_quiet_hours` | `HH:MM-HH:MM` — no proactive speech in this window (supports crossing midnight) | `22:30-07:30` |
| `proactive_morning_briefing` | `HH:MM` — daily spoken briefing: today's agenda + unread mail | `08:30` |
| `proactive_email_checks` | List of `HH:MM` — speaks ONLY if there are unread emails | `["12:00", "15:30", "18:00"]` |
| `proactive_event_reminders_min` | Minutes before each agenda event to remind | `[60, 15]` |
| `proactive_legal_radar_enabled` | `true` / `false` — scan unread e-mail for court/tribunal deadlines (Projudi/PJe/TJ), create calendar events and brief you | `false` |
| `proactive_legal_radar_slots` | List of `HH:MM` to run the legal radar (defaults to `proactive_email_checks`) | `["12:00", "15:30", "18:00"]` |
| `aec_enabled` | `true` / `false` — WebRTC echo cancellation via PipeWire (mic stays open while Jarvis speaks) | `true` |
| `voice_barge_in` | `true` / `false` / `auto` — interrupt speech by voice; `auto` = on when AEC is active | `auto` |
| `echo_guard_cooldown_sec` | Post-speech mic cooldown when AEC is active | `0.6` |

---

## Google Integration (Gmail API + Calendar — optional)

Real Google Calendar in briefings/reminders and advanced Gmail search
(`from:`, `subject:`, `newer_than:`) with mark-as-read. One-time setup:

1. https://console.cloud.google.com → create a project (e.g. "Jarvis")
2. APIs & Services → Library → enable **Gmail API** and **Google Calendar API**
3. OAuth consent screen → External → fill name/email → **Publish app**
   (in "Testing" status tokens expire every 7 days)
4. Credentials → Create credentials → OAuth client ID → **Desktop app** →
   download the JSON → save as `config/google_credentials.json`
5. Run: `.venv/bin/python scripts/setup_google.py` (browser consent, once)

Without it, everything still works via IMAP/app-password and the local agenda.

---

## Built-in Tools

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

## License

MIT — FatihMakes Industries
