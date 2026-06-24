"""
Multi-provider LLM client for MARK XL.

Supports:
  "ollama"        — Local Ollama (default)
  "openai"        — OpenAI API (GPT-4o, GPT-4o-mini, etc.)
  "anthropic"     — Anthropic API (Claude 3.5 Sonnet, Claude 3 Haiku, etc.)
  "openrouter"    — OpenRouter (access to 100+ models via single API)
  "ollama_cloud"  — Ollama Cloud API
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Generator

import requests
from requests.adapters import HTTPAdapter

from core.logger import get_logger
from core.paths import API_CONFIG_PATH as CONFIG_PATH

log = get_logger("llm")

_SENT_END = re.compile(r'(?<=[.!?])\s+|(?<=\n)\s*\n')

_DEFAULTS = {
    "llm_url":      "http://localhost:11434",
    "llm_model":    "qwen3.5:4b",
    "llm_provider": "ollama",
}

# ── Provider configs ──────────────────────────────────────────────────────

PROVIDER_CONFIGS = {
    "ollama": {
        "name": "Ollama (Local)",
        "url": "http://localhost:11434",
        "models": [
            "qwen3.5:4b", "qwen3.5:2b", "qwen3.5:9b", "qwen3.5:0.8b",
            "llama3.2", "llama3.1:8b", "mistral", "phi3",
            "gemma2:9b", "gemma2:2b",
        ],
        "requires_key": False,
    },
    "openai": {
        "name": "OpenAI",
        "url": "https://api.openai.com/v1",
        "models": [
            "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4",
            "gpt-3.5-turbo", "o1-preview", "o1-mini",
        ],
        "requires_key": True,
        "key_name": "openai_api_key",
    },
    "anthropic": {
        "name": "Anthropic (Claude)",
        "url": "https://api.anthropic.com",
        "models": [
            "claude-sonnet-4-20250514", "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022", "claude-3-opus-20240229",
            "claude-3-sonnet-20240229", "claude-3-haiku-20240307",
        ],
        "requires_key": True,
        "key_name": "anthropic_api_key",
    },
    "openrouter": {
        "name": "OpenRouter",
        "url": "https://openrouter.ai/api/v1",
        "models": [
            "openai/gpt-4o", "openai/gpt-4o-mini", "openai/gpt-4-turbo",
            "anthropic/claude-3.5-sonnet", "anthropic/claude-3-haiku",
            "google/gemini-pro-1.5", "google/gemini-flash-1.5",
            "meta-llama/llama-3.1-70b-instruct", "meta-llama/llama-3.1-8b-instruct",
            "qwen/qwen-2.5-72b-instruct", "qwen/qwen-2.5-7b-instruct",
            "mistralai/mistral-large", "mistralai/mixtral-8x7b-instruct",
            "deepseek/deepseek-chat", "deepseek/deepseek-coder",
        ],
        "requires_key": True,
        "key_name": "openrouter_api_key",
    },
    "ollama_cloud": {
        "name": "Ollama Cloud",
        "url": "https://ollama.com/v1",
        "models": ["qwen3-coder:480b-cloud", "gpt-oss:120b-cloud"],
        "requires_key": True,
        "key_name": "ollama_api_key",
    },
    "google": {
        "name": "Google AI (Gemini)",
        "url": "https://generativelanguage.googleapis.com/v1beta",
        "models": [
            "gemini-1.5-pro", "gemini-1.5-flash",
            "gemini-1.0-pro", "gemini-pro",
        ],
        "requires_key": True,
        "key_name": "google_api_key",
    },
}

# ── Connection pooling ────────────────────────────────────────────────────
_session = requests.Session()
_session.headers.update({"Connection": "keep-alive"})
_adapter = HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=0)
_session.mount("http://", _adapter)
_session.mount("https://", _adapter)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_llm_settings() -> tuple[str, str]:
    cfg = _load_config()
    provider = cfg.get("llm_provider", "ollama").strip().lower()
    url = cfg.get("llm_url", _DEFAULTS["llm_url"]).rstrip("/")
    model = cfg.get("llm_model", _DEFAULTS["llm_model"])

    # Auto-set URL if not explicitly configured
    if provider in PROVIDER_CONFIGS and not cfg.get("llm_url"):
        url = PROVIDER_CONFIGS[provider]["url"]

    return url, model


def get_llm_provider() -> str:
    raw = _load_config().get("llm_provider", "ollama").strip().lower()
    if raw in ("openai",):
        return "openai"
    if raw in ("anthropic", "claude"):
        return "anthropic"
    if raw in ("openrouter",):
        return "openrouter"
    if raw in ("ollama_cloud", "ollamacloud"):
        return "ollama_cloud"
    if raw in ("google", "gemini"):
        return "google"
    return "ollama"


def get_provider_info() -> dict:
    """Get current provider info for display."""
    provider = get_llm_provider()
    url, model = get_llm_settings()
    config = PROVIDER_CONFIGS.get(provider, {})
    return {
        "provider": provider,
        "name": config.get("name", provider),
        "url": url,
        "model": model,
        "requires_key": config.get("requires_key", False),
        "available_models": config.get("models", []),
    }


def _get_api_key(provider: str) -> str:
    """Get API key for the specified provider."""
    cfg = _load_config()
    config = PROVIDER_CONFIGS.get(provider, {})
    key_name = config.get("key_name", "")

    # Check config first, then environment
    key = cfg.get(key_name, "") or os.environ.get(key_name.upper(), "")
    return key


def _get_auth_headers(provider: str) -> dict:
    """Get auth headers for the specified provider."""
    key = _get_api_key(provider)
    if not key:
        return {}

    if provider == "anthropic":
        return {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
    return {"Authorization": f"Bearer {key}"}


def _get_fallback_model() -> str:
    return _load_config().get("llm_fallback_model", "").strip()


def _is_model_not_found(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in ("not found", "pull the model", "model", "404", "doesn't exist"))


# ---------------------------------------------------------------------------
# Ollama lifecycle
# ---------------------------------------------------------------------------

def ensure_ollama_running(timeout: int = 15) -> bool:
    provider = get_llm_provider()
    url, _ = get_llm_settings()

    # Non-Ollama providers don't need Ollama running
    if provider not in ("ollama",):
        return True

    health = f"{url}/api/tags"

    def _is_up() -> bool:
        try:
            return _session.get(health, timeout=3).status_code == 200
        except Exception:
            return False

    if _is_up():
        return True

    log.info("Ollama not running — launching 'ollama serve'")
    try:
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.Popen(["ollama", "serve"], **kwargs)
    except FileNotFoundError:
        log.error("'ollama' command not found. Install from https://ollama.com")
        return False
    except Exception as e:
        log.error("Could not launch Ollama: %s", e)
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1.0)
        if _is_up():
            log.info("Ollama started successfully.")
            return True

    log.error("Ollama did not respond within the timeout.")
    return False


def warmup_model(system_prompt: str | None = None) -> bool:
    url, model = get_llm_settings()
    provider = get_llm_provider()
    log.info("Warming up '%s' (%s)", model, provider)

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": "hi"})

    if provider == "ollama":
        payload = {
            "model": model, "messages": messages,
            "stream": False, "keep_alive": -1, "think": False,
            "options": {"num_predict": 1, "num_ctx": 4096, "num_gpu": 99},
        }
        try:
            _session.post(f"{url}/api/chat", json=payload, timeout=180).raise_for_status()
            log.info("'%s' loaded and KV cache primed.", model)
            return True
        except Exception as e:
            log.warning("Warmup failed (non-fatal): %s", e)
            return False

    # Cloud providers — just test connectivity
    headers = _get_auth_headers(provider)
    if provider == "anthropic":
        payload = {
            "model": model, "messages": messages,
            "max_tokens": 1,
        }
        try:
            _session.post(f"{url}/v1/messages", json=payload, headers=headers, timeout=30).raise_for_status()
            log.info("'%s' ready.", model)
            return True
        except Exception as e:
            log.warning("Warmup failed: %s", e)
            return False

    if provider == "google":
        # Google Gemini API uses different format
        api_key = _get_api_key(provider)
        gemini_url = f"{url}/models/{model}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": "hi"}]}],
            "generationConfig": {"maxOutputTokens": 1},
        }
        try:
            _session.post(gemini_url, json=payload, timeout=30).raise_for_status()
            log.info("'%s' ready.", model)
            return True
        except Exception as e:
            log.warning("Warmup failed: %s", e)
            return False

    # OpenAI-compatible (openai, openrouter, ollama_cloud)
    payload = {
        "model": model, "messages": messages,
        "stream": False, "max_tokens": 1,
    }
    try:
        _session.post(f"{url}/v1/chat/completions", json=payload, headers=headers, timeout=30).raise_for_status()
        log.info("'%s' ready.", model)
        return True
    except Exception as e:
        log.warning("Warmup failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Response parsing helpers
# ---------------------------------------------------------------------------

def _parse_openai_tool_calls(raw_tc: list) -> list[dict]:
    result = []
    for t in (raw_tc or []):
        args = t.get("function", {}).get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                pass
        result.append({
            "id": t.get("id", ""),
            "function": {"name": t["function"]["name"], "arguments": args},
        })
    return result


def _parse_anthropic_response(data: dict) -> dict:
    """Parse Anthropic Messages API response."""
    content = ""
    tool_calls = []
    for block in data.get("content", []):
        if block.get("type") == "text":
            content += block.get("text", "")
        elif block.get("type") == "tool_use":
            tool_calls.append({
                "id": block.get("id", ""),
                "function": {
                    "name": block.get("name", ""),
                    "arguments": block.get("input", {}),
                },
            })
    return {"content": content.strip(), "tool_calls": tool_calls}


# ---------------------------------------------------------------------------
# Anthropic messages formatting
# ---------------------------------------------------------------------------

def _format_anthropic_messages(messages: list) -> tuple[str, list]:
    """Convert OpenAI-format messages to Anthropic format.
    Returns (system_prompt, messages_list)."""
    system = ""
    anthropic_msgs = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "system":
            system = content
        elif role == "user":
            anthropic_msgs.append({"role": "user", "content": content})
        elif role == "assistant":
            entry = {"role": "assistant", "content": content}
            if msg.get("tool_calls"):
                # Anthropic uses content blocks for tool use
                blocks = [{"type": "text", "text": content}] if content else []
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": fn.get("arguments", {}),
                    })
                entry["content"] = blocks
            anthropic_msgs.append(entry)
        elif role == "tool":
            anthropic_msgs.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": content,
                }],
            })

    return system, anthropic_msgs


def _convert_tools_to_anthropic(tools: list) -> list:
    """Convert OpenAI-format tools to Anthropic format."""
    anthropic_tools = []
    for t in tools:
        fn = t.get("function", {})
        anthropic_tools.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return anthropic_tools


# ---------------------------------------------------------------------------
# Non-streaming chat
# ---------------------------------------------------------------------------

def call_llm(
    messages: list,
    tools: list | None = None,
    timeout: int = 120,
) -> dict:
    url, model = get_llm_settings()
    provider = get_llm_provider()
    headers = _get_auth_headers(provider)

    # ── Anthropic ─────────────────────────────────────────────────────────
    if provider == "anthropic":
        system, anthropic_msgs = _format_anthropic_messages(messages)
        payload = {
            "model": model,
            "messages": anthropic_msgs,
            "max_tokens": 500,
            "stream": False,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = _convert_tools_to_anthropic(tools)
        try:
            resp = _session.post(f"{url}/v1/messages", json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return _parse_anthropic_response(resp.json())
        except Exception as e:
            raise RuntimeError(f"Anthropic API error: {e}") from e

    # ── OpenAI / OpenRouter / Ollama Cloud ────────────────────────────────
    if provider in ("openai", "openrouter", "ollama_cloud"):
        payload = {"model": model, "messages": messages, "stream": False, "max_tokens": 500}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            resp = _session.post(f"{url}/v1/chat/completions", json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            msg = data.get("choices", [{}])[0].get("message", {})
            return {
                "content": (msg.get("content") or "").strip(),
                "tool_calls": _parse_openai_tool_calls(msg.get("tool_calls")),
            }
        except Exception as e:
            raise RuntimeError(f"{provider} API error: {e}") from e

    # ── Google Gemini ─────────────────────────────────────────────────────
    if provider == "google":
        api_key = _get_api_key(provider)
        gemini_url = f"{url}/models/{model}:generateContent?key={api_key}"
        
        # Convert messages to Gemini format
        contents = []
        system_instruction = None
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                system_instruction = {"parts": [{"text": content}]}
            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": content}]})
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": content}]})
        
        payload = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": 500,
                "temperature": 0.7,
            },
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction
        
        try:
            resp = _session.post(gemini_url, json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            # Extract text from Gemini response
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                text = " ".join(p.get("text", "") for p in parts)
                return {"content": text.strip(), "tool_calls": []}
            return {"content": "", "tool_calls": []}
        except Exception as e:
            raise RuntimeError(f"Google Gemini API error: {e}") from e

    # ── Ollama (local) ────────────────────────────────────────────────────
    payload = {
        "model": model, "messages": messages,
        "stream": False, "keep_alive": -1, "think": False,
        "options": {"num_predict": 500, "num_gpu": 99, "num_ctx": 4096},
    }
    if tools:
        payload["tools"] = tools
    try:
        resp = _session.post(f"{url}/api/chat", json=payload, timeout=timeout)
        resp.raise_for_status()
        msg = resp.json().get("message", {})
        return {"content": (msg.get("content") or "").strip(), "tool_calls": msg.get("tool_calls") or []}
    except requests.exceptions.ConnectionError:
        if ensure_ollama_running():
            resp = _session.post(f"{url}/api/chat", json=payload, timeout=timeout)
            resp.raise_for_status()
            msg = resp.json().get("message", {})
            return {"content": (msg.get("content") or "").strip(), "tool_calls": msg.get("tool_calls") or []}
        raise RuntimeError(f"Cannot connect to Ollama at {url}.")
    except Exception as e:
        raise RuntimeError(f"Ollama error: {e}") from e


def call_llm_text(prompt: str, system: str | None = None, model: str | None = None, timeout: int = 120) -> str:
    url, default_model = get_llm_settings()
    provider = get_llm_provider()
    m = model or default_model

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    result = call_llm(messages, timeout=timeout)
    return result.get("content", "")


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

def _stream_sse(url, endpoint, payload, headers, timeout, provider, label):
    full_content = ""
    buf = ""
    tc_fragments: dict = {}

    with _session.post(endpoint, json=payload, headers=headers, timeout=timeout, stream=True) as resp:
        resp.raise_for_status()
        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue

            choice = chunk.get("choices", [{}])[0]
            delta = choice.get("delta", {})
            text = delta.get("content") or ""
            full_content += text
            buf += text

            while True:
                m = _SENT_END.search(buf)
                if not m:
                    break
                sentence = buf[:m.start() + 1].strip()
                buf = buf[m.end():]
                if sentence:
                    yield {"type": "sentence", "text": sentence}

            for tc in (delta.get("tool_calls") or []):
                idx = tc.get("index", 0)
                if idx not in tc_fragments:
                    tc_fragments[idx] = {"id": "", "function": {"name": "", "arguments": ""}}
                frag = tc_fragments[idx]
                frag["id"] = frag["id"] or tc.get("id", "")
                fn = tc.get("function", {})
                frag["function"]["name"] += fn.get("name") or ""
                frag["function"]["arguments"] += fn.get("arguments") or ""

            finish = choice.get("finish_reason")
            if finish in ("stop", "tool_calls", "length"):
                break

    if buf.strip():
        yield {"type": "sentence", "text": buf.strip()}

    tool_calls = _parse_openai_tool_calls([
        {"id": f["id"], "function": {"name": f["function"]["name"], "arguments": f["function"]["arguments"]}}
        for f in (tc_fragments[i] for i in sorted(tc_fragments))
    ])
    yield {"type": "done", "content": full_content.strip(), "tool_calls": tool_calls}


def _stream_anthropic(url, payload, headers, timeout):
    """Stream Anthropic Messages API."""
    full_content = ""
    buf = ""
    tool_calls = []

    with _session.post(f"{url}/v1/messages", json=payload, headers=headers, timeout=timeout, stream=True) as resp:
        resp.raise_for_status()
        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue

            etype = event.get("type", "")

            if etype == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    full_content += text
                    buf += text
                    while True:
                        m = _SENT_END.search(buf)
                        if not m:
                            break
                        sentence = buf[:m.start() + 1].strip()
                        buf = buf[m.end():]
                        if sentence:
                            yield {"type": "sentence", "text": sentence}
                elif delta.get("type") == "input_json_delta":
                    # Tool use input streaming — accumulate
                    pass

            elif etype == "content_block_start":
                block = event.get("content_block", {})
                if block.get("type") == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", ""),
                        "function": {"name": block.get("name", ""), "arguments": block.get("input", {})},
                    })

            elif etype == "message_stop":
                break

    if buf.strip():
        yield {"type": "sentence", "text": buf.strip()}
    yield {"type": "done", "content": full_content.strip(), "tool_calls": tool_calls}


def call_llm_stream(
    messages: list,
    tools: list | None = None,
    timeout: int = 120,
) -> Generator[dict, None, None]:
    url, model = get_llm_settings()
    provider = get_llm_provider()
    headers = _get_auth_headers(provider)

    # ── Anthropic ─────────────────────────────────────────────────────────
    if provider == "anthropic":
        system, anthropic_msgs = _format_anthropic_messages(messages)
        payload = {
            "model": model, "messages": anthropic_msgs,
            "max_tokens": 500, "stream": True,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = _convert_tools_to_anthropic(tools)
        try:
            yield from _stream_anthropic(url, payload, headers, timeout)
        except Exception as e:
            raise RuntimeError(f"Anthropic stream error: {e}") from e
        return

    # ── OpenAI / OpenRouter / Ollama Cloud ────────────────────────────────
    if provider in ("openai", "openrouter", "ollama_cloud"):
        payload = {"model": model, "messages": messages, "stream": True, "max_tokens": 500}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            yield from _stream_sse(url, f"{url}/v1/chat/completions", payload, headers, timeout, provider, provider)
        except Exception as e:
            raise RuntimeError(f"{provider} stream error: {e}") from e
        return

    # ── Google Gemini ─────────────────────────────────────────────────────
    if provider == "google":
        api_key = _get_api_key(provider)
        gemini_url = f"{url}/models/{model}:streamGenerateContent?alt=sse&key={api_key}"
        
        # Convert messages to Gemini format
        contents = []
        system_instruction = None
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                system_instruction = {"parts": [{"text": content}]}
            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": content}]})
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": content}]})
        
        payload = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": 500,
                "temperature": 0.7,
            },
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction
        
        try:
            with _session.post(gemini_url, json=payload, timeout=timeout, stream=True) as resp:
                resp.raise_for_status()
                full_content = ""
                buf = ""
                for raw in resp.iter_lines():
                    if not raw:
                        continue
                    line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    
                    # Extract text from Gemini streaming response
                    candidates = chunk.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        for part in parts:
                            text = part.get("text", "")
                            if text:
                                full_content += text
                                buf += text
                                while True:
                                    m = _SENT_END.search(buf)
                                    if not m:
                                        break
                                    sentence = buf[:m.start() + 1].strip()
                                    buf = buf[m.end():]
                                    if sentence:
                                        yield {"type": "sentence", "text": sentence}
                
                if buf.strip():
                    yield {"type": "sentence", "text": buf.strip()}
                yield {"type": "done", "content": full_content.strip(), "tool_calls": []}
        except Exception as e:
            raise RuntimeError(f"Google Gemini stream error: {e}") from e
        return

    # ── Ollama (local) ────────────────────────────────────────────────────
    payload = {
        "model": model, "messages": messages,
        "stream": True, "keep_alive": -1, "think": False,
        "options": {"num_predict": 500, "num_gpu": 99, "num_ctx": 4096},
    }
    if tools:
        payload["tools"] = tools

    def _do_native_stream():
        with _session.post(f"{url}/api/chat", json=payload, timeout=timeout, stream=True) as resp:
            resp.raise_for_status()
            full_content = ""
            tool_calls = []
            buf = ""
            for raw in resp.iter_lines():
                if not raw:
                    continue
                try:
                    chunk = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                msg = chunk.get("message", {})
                delta = msg.get("content") or ""
                full_content += delta
                buf += delta
                while True:
                    m = _SENT_END.search(buf)
                    if not m:
                        break
                    sentence = buf[:m.start() + 1].strip()
                    buf = buf[m.end():]
                    if sentence:
                        yield {"type": "sentence", "text": sentence}
                tc = msg.get("tool_calls")
                if tc:
                    tool_calls.extend(tc)
                if chunk.get("done"):
                    if buf.strip():
                        yield {"type": "sentence", "text": buf.strip()}
                    yield {"type": "done", "content": full_content.strip(), "tool_calls": tool_calls}
                    return

    try:
        yield from _do_native_stream()
    except requests.exceptions.ConnectionError:
        if ensure_ollama_running():
            yield from _do_native_stream()
            return
        raise RuntimeError(f"Cannot connect to Ollama at {url}.")
    except Exception as e:
        raise RuntimeError(f"Ollama stream error: {e}")
