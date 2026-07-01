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
Microphone → STT (Deepgram Live / Whisper / Vosk)
                  ↓
      LLM (Groq / Ollama Cloud / Ollama Local / OpenAI-compatible)
                  ↓
         Tool Execution (OS, Browser, Files …)
                  ↓
           TTS (ElevenLabs / EdgeTTS / Kokoro)
                  ↓
              Speaker
```

### Core Components

| Layer | Technology | Notes |
|-------|-----------|-------|
| **STT** | Deepgram Live / faster-whisper / Vosk | Hybrid low-latency mode (cloud) + offline fallback. |
| **LLM** | Groq / Ollama Cloud / Ollama / OpenAI-compatible | Fast chat route + power route with streaming + tool calling. |
| **TTS** | ElevenLabs / EdgeTTS / Kokoro | ElevenLabs = lowest latency, Kokoro = fully offline. |
| **UI** | PyQt6 | HUD overlay with system monitor, log panel, file drop zone. |
| **Agent** | Custom task queue | Multi-step planner + executor + error recovery. |

---

## Features

- **Streaming responses** — TTS starts speaking on the first sentence, not after the full response
- **Tool calling** — 30+ built-in tools: browser control, file management, weather, YouTube, messaging, screen analysis, code helper, game updater, flight finder, calendar, translator, notes, smart home, and more
- **Long-term memory** — Silently saves personal facts; recalled in every conversation
- **Live configuration** — Change LLM model, STT engine, TTS voice without restarting (⚙ Configure button)
- **Ollama auto-start** — Automatically launches `ollama serve` if it's not running
- **Model warmup** — Pre-loads the LLM during startup so the first message is as fast as subsequent ones
- **Turn cancellation (voice)** — New voice command supersedes old turn and cancels stale TTS queue/output
- **Latency telemetry** — Logs STT, first-token, first-sentence, and first-audio timing per voice turn
- **Proactive mode (optional)** — Idle-time nudges for next actions (calendar, health check, reminders)
- **Multi-language STT** — Set `stt_language` to `auto` (Whisper detects) or a specific locale (`tr`, `de`, `fr`, …)
- **Cross-platform** — Windows, macOS, Linux (OS detected automatically at runtime)
- **File drop zone** — Drag and drop images, PDFs, Word docs, CSV, audio, video for AI processing

---

## Requirements

- Python 3.11 or 3.12
- For local LLM mode: [Ollama](https://ollama.com) installed and a model pulled (e.g. `ollama pull qwen2.5:7b`)
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
    "stt_engine":         "deepgram",
    "stt_model":          "small",
    "stt_language":       "pt",
    "deepgram_api_key":   "YOUR_DEEPGRAM_KEY",
    "deepgram_model":     "nova-2",
    "deepgram_endpointing_ms": 300,
    "proactive_mode":      false,
    "proactive_interval_sec": 900,
    "llm_provider":       "groq",
    "llm_url":            "https://api.groq.com/openai/v1",
    "llm_model":          "llama-3.3-70b-versatile",
    "groq_api_key":       "YOUR_GROQ_API_KEY",
    "brave_api_key":      "YOUR_BRAVE_SEARCH_API_KEY",
    "brave_search_country": "BR",
    "vision_model":       "llava:7b",
    "tts_engine":         "elevenlabs",
    "tts_voice":          "GIuLCSVfgJaUuh7hYOY8",
    "elevenlabs_api_key": "YOUR_ELEVENLABS_KEY",
    "allow_code_execution": false
}
```

| Key | Values | Default |
|-----|--------|---------|
| `stt_engine` | `deepgram` / `whisper` / `vosk` | `deepgram` (first boot) |
| `stt_model` | `tiny` / `base` / `small` / `medium` / `large-v3` | `base` |
| `stt_language` | `auto` or ISO code (`tr`, `en`, `de` …) | `auto` |
| `deepgram_api_key` | Deepgram token | — |
| `deepgram_endpointing_ms` | endpointing in ms for live STT | `300` |
| `proactive_mode` | enable idle proactive suggestions | `false` |
| `proactive_interval_sec` | idle seconds before proactive nudge | `900` |
| `llm_provider` | `groq` / `ollama` / `ollama_cloud` / `openai` | `groq` (first boot) |
| `llm_url` | API base URL | `https://api.groq.com/openai/v1` (first boot) |
| `llm_model` | Model name | `llama-3.3-70b-versatile` (first boot) |
| `groq_api_key` | Groq bearer token | — |
| `brave_api_key` | Brave Search API token (optional for web_search) | — |
| `brave_search_country` | Country code for Brave search ranking | `BR` |
| `ollama_api_key` | Ollama Cloud bearer token | — |
| `tts_engine` | `edgetts` / `kokoro` / `elevenlabs` | `elevenlabs` (first boot) |
| `tts_voice` | Voice name / ID depending on engine | `GIuLCSVfgJaUuh7hYOY8` (first boot) |

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
