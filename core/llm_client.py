"""
Local LLM client for MARK XL.

Supports three backends — selected via  "llm_provider"  in config/api_keys.json:

  "llm_provider": "ollama"   (default)
        Uses Ollama's native /api/chat endpoint.
        Download: https://ollama.com
        Default port: 11434

  "llm_provider": "ollama_cloud"
        Uses Ollama Cloud API (https://ollama.com/v1).
        Requires "ollama_api_key" in config.
        Models: qwen3-coder:480b-cloud, gpt-oss:120b-cloud, etc.

  "llm_provider": "openai"
        Uses any OpenAI-compatible server: LM Studio, Jan, LocalAI,
        llama.cpp server, vLLM, etc.
        LM Studio download: https://lmstudio.ai   (default port: 1234)
        Set  "llm_url": "http://localhost:1234"  in config.
        Note: tool-calling support depends on the model; use a model that
        supports function/tool calls (e.g. Qwen2.5, Llama-3.1, Mistral).

  "llm_provider": "groq"
        Uses Groq's OpenAI-compatible API (https://api.groq.com/openai/v1).
        Requires "groq_api_key" in config.
        Models: llama-3.3-70b-versatile, llama-3.1-8b-instant, etc.
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

from core.logger import get_logger
from core.paths import API_CONFIG_PATH as CONFIG_PATH

log = get_logger("llm")

_SENT_END = re.compile(r'(?<=[.!?])\s+|(?<=\n)\s*\n')

_DEFAULTS = {
    "llm_url":             "http://localhost:11434",
    "llm_model":           "llama3.2",
    "llm_provider":        "ollama",
    "llm_fallback_model":  "",
}

# Token budget — chat uses a small cap for speed; tool rounds need headroom.
_STREAM_MAX_TOKENS = 1024
_STREAM_CHAT_TOKENS = 180
_CHAT_MAX_TOKENS   = 1024


def _openai_base(url: str) -> str:
    """Normalize base URL — accept both https://host and https://host/v1."""
    return url.rstrip("/")


def _openai_models_url(url: str) -> str:
    base = _openai_base(url)
    return f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"


def _openai_chat_url(url: str) -> str:
    base = _openai_base(url)
    return f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _default_url_for_provider(provider: str) -> str:
    return {
        "groq":         "https://api.groq.com/openai/v1",
        "ollama_cloud": "https://ollama.com",
        "openai":       "http://localhost:1234",
        "ollama":       "http://localhost:11434",
    }.get(provider, _DEFAULTS["llm_url"])


def get_fast_llm_model() -> str:
    """Fast model for casual chat (low latency). Falls back to llm_model."""
    cfg = _load_config()
    primary = cfg.get("llm_model", _DEFAULTS["llm_model"])
    return (
        cfg.get("llm_fast_model", "").strip()
        or cfg.get("llm_fallback_model", "").strip()
        or primary
    )


def get_chat_llm_config() -> tuple[str, str, str]:
    """
    Low-latency path for casual chat: (base_url, model, provider).

    When llm_chat_provider is set (or Groq key exists while primary is slower
    cloud), routes chat to a fast model instead of the power model.
    """
    cfg = _load_config()
    primary = get_llm_provider()

    raw_chat = cfg.get("llm_chat_provider", "").strip().lower()
    if raw_chat in ("groq", "ollama_cloud", "openai", "ollama"):
        chat_provider = raw_chat
    elif cfg.get("groq_api_key", "").strip() and primary != "groq":
        chat_provider = "groq"
    else:
        chat_provider = primary

    default_url = _default_url_for_provider(chat_provider)
    url = cfg.get("llm_url", default_url).rstrip("/") if chat_provider == primary else default_url

    if chat_provider == "groq":
        model = (
            cfg.get("llm_fast_model", "").strip()
            or cfg.get("llm_fallback_model", "").strip()
            or "llama-3.1-8b-instant"
        )
    else:
        model = get_fast_llm_model()

    return url, model, chat_provider


def get_power_llm_model() -> str:
    """Powerful model for tool-calling / complex tasks."""
    cfg = _load_config()
    return cfg.get("llm_power_model", "").strip() or cfg.get("llm_model", _DEFAULTS["llm_model"])


def get_llm_settings() -> tuple[str, str]:
    """Returns (base_url, model_name)."""
    cfg      = _load_config()
    provider = get_llm_provider()
    url   = cfg.get("llm_url", _default_url_for_provider(provider)).rstrip("/")
    model = cfg.get("llm_model", _DEFAULTS["llm_model"])
    return url, model


def get_llm_provider() -> str:
    """Returns 'ollama', 'ollama_cloud', 'groq', or 'openai'."""
    raw = _load_config().get("llm_provider", "ollama").strip().lower()
    if raw == "groq":
        return "groq"
    if raw in ("openai", "lmstudio", "localai", "jan", "llamacpp"):
        return "openai"
    if raw in ("ollama_cloud", "ollamacloud"):
        return "ollama_cloud"
    return "ollama"


def _is_openai_compat(provider: str) -> bool:
    return provider in ("ollama_cloud", "openai", "groq")


def _get_fallback_model() -> str:
    return _load_config().get("llm_fallback_model", "").strip()


def _is_tool_use_error(response: requests.Response) -> bool:
    """Groq returns 400 with code tool_use_failed when tools confuse the model."""
    if response.status_code not in (400, 422):
        return False
    try:
        code = response.json().get("error", {}).get("code", "")
        return code in ("tool_use_failed", "invalid_request_error")
    except Exception:
        return response.status_code == 400


def _is_model_not_found(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in ("not found", "pull the model", "model", "404", "doesn't exist"))


def _get_auth_headers(provider: str | None = None) -> dict:
    """Bearer token for cloud / OpenAI-compatible providers."""
    provider = provider or get_llm_provider()
    cfg = _load_config()
    key = ""
    if provider == "ollama_cloud":
        key = cfg.get("ollama_api_key", "") or os.environ.get("OLLAMA_API_KEY", "")
    elif provider == "groq":
        key = cfg.get("groq_api_key", "") or os.environ.get("GROQ_API_KEY", "")
    elif provider == "openai":
        key = cfg.get("openai_api_key", "") or os.environ.get("OPENAI_API_KEY", "")
    return {"Authorization": f"Bearer {key}"} if key else {}


def _provider_label(provider: str) -> str:
    return {
        "ollama_cloud": "Ollama Cloud API",
        "groq":         "Groq API",
        "openai":       "OpenAI-compatible server",
        "ollama":       "Ollama",
    }.get(provider, provider)


# ---------------------------------------------------------------------------
# Ollama lifecycle
# ---------------------------------------------------------------------------

def ensure_ollama_running(timeout: int = 15) -> bool:
    """Ping or auto-launch Ollama.  Returns True if reachable."""
    url, _   = get_llm_settings()
    provider = get_llm_provider()

    if _is_openai_compat(provider):
        health = _openai_models_url(url)
        headers = _get_auth_headers(provider)
        try:
            ok = requests.get(health, headers=headers, timeout=5).status_code == 200
            label = _provider_label(provider)
            log.info("%s %s at %s", label, "reachable" if ok else "returned non-200", url)
            return ok
        except Exception as e:
            log.warning("Cannot reach %s at %s: %s", _provider_label(provider), url, e)
            return False

    # Native Ollama
    health = f"{url}/api/tags"

    def _is_up() -> bool:
        try:
            return requests.get(health, timeout=3).status_code == 200
        except Exception:
            return False

    if _is_up():
        return True

    log.info("Ollama not running — launching 'ollama serve'")
    try:
        kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
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
    """
    Pre-load the model AND prime Ollama's KV prefix cache.

    Pass the *static* part of the system prompt so the prefix stays valid
    across calls → first-token <1 s after warmup.
    """
    url, model = get_llm_settings()
    provider   = get_llm_provider()
    log.info("Warming up '%s' (%s)", model, provider)

    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": "hi"})

    if _is_openai_compat(provider):
        payload: dict = {
            "model": model, "messages": messages,
            "stream": False, "max_tokens": 1,
        }
        headers = _get_auth_headers(provider)
        try:
            resp = requests.post(_openai_chat_url(url), json=payload, headers=headers, timeout=180)
            resp.raise_for_status()
            log.info("'%s' ready.", model)
            return True
        except Exception as e:
            log.warning("Warmup failed (non-fatal): %s", e)
            return False

    # Native Ollama — include keep_alive + GPU hint for cache priming
    payload = {
        "model": model, "messages": messages,
        "stream": False, "keep_alive": -1,
        "options": {"num_predict": 1, "num_gpu": 99},
    }
    try:
        resp = requests.post(f"{url}/api/chat", json=payload, timeout=180)
        resp.raise_for_status()
        log.info("'%s' loaded and KV cache primed.", model)
        return True
    except Exception as e:
        print(f"[LLM] Warmup failed (non-fatal): {e}")
        return False


# ---------------------------------------------------------------------------
# Response parsing helpers
# ---------------------------------------------------------------------------

def _parse_openai_tool_calls(raw_tc: list) -> list[dict]:
    """Normalise OpenAI-style tool_calls to Ollama-style format."""
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
            "function": {
                "name": t["function"]["name"],
                "arguments": args,
            },
        })
    return result


def _parse_openai_response(data: dict) -> dict:
    """Extract content + tool_calls from an OpenAI-format response body."""
    choice = data.get("choices", [{}])[0]
    msg    = choice.get("message", {})
    return {
        "content":    (msg.get("content") or "").strip(),
        "tool_calls": _parse_openai_tool_calls(msg.get("tool_calls")),
    }


def _parse_ollama_response(data: dict) -> dict:
    """Extract content + tool_calls from an Ollama native response body."""
    msg = data.get("message", {})
    return {
        "content":    (msg.get("content") or "").strip(),
        "tool_calls": msg.get("tool_calls") or [],
    }


# ---------------------------------------------------------------------------
# Non-streaming chat
# ---------------------------------------------------------------------------

def call_llm(
    messages: list,
    tools:    list | None = None,
    timeout:  int = 120,
) -> dict:
    """
    Non-streaming chat request.  Routes to Ollama, Ollama Cloud, or OpenAI-compatible.

    Returns: {"content": str, "tool_calls": list}
    """
    url, model = get_llm_settings()
    provider   = get_llm_provider()
    endpoint   = _openai_chat_url(url) if provider != "ollama" else f"{url}/api/chat"
    headers    = _get_auth_headers(provider)

    if _is_openai_compat(provider):
        payload: dict = {
            "model": model, "messages": messages,
            "stream": False, "max_tokens": _CHAT_MAX_TOKENS,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            resp = requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return _parse_openai_response(resp.json())
        except Exception as e:
            raise RuntimeError(f"OpenAI-compatible LLM call failed: {e}") from e

    # Native Ollama
    payload = {
        "model": model, "messages": messages,
        "stream": False, "keep_alive": -1,
        "options": {"num_predict": _CHAT_MAX_TOKENS, "num_gpu": 99},
    }
    if tools:
        payload["tools"] = tools

    try:
        resp = requests.post(endpoint, json=payload, timeout=timeout)
        resp.raise_for_status()
        return _parse_ollama_response(resp.json())
    except requests.exceptions.ConnectionError as e:
        log.warning("ConnectionError — trying to restart Ollama: %s", e)
        if ensure_ollama_running():
            try:
                resp = requests.post(endpoint, json=payload, timeout=timeout)
                resp.raise_for_status()
                return _parse_ollama_response(resp.json())
            except Exception:
                pass
        raise RuntimeError(
            f"Cannot connect to Ollama at {url}. "
            "Make sure Ollama is installed and run: ollama serve"
        ) from None
    except requests.exceptions.Timeout:
        raise RuntimeError("Ollama request timed out after 120 s.") from None
    except requests.exceptions.HTTPError as e:
        log.error("HTTPError: %s — %s", e.response.status_code, e.response.text[:200])
        raise RuntimeError(f"Ollama HTTP error: {e.response.status_code}") from e
    except Exception as e:
        log.error("Unexpected error: %s: %s", type(e).__name__, e)
        raise RuntimeError(f"LLM call failed: {e}") from e


def call_llm_text(
    prompt:  str,
    system:  str | None = None,
    model:   str | None = None,
    timeout: int = 120,
) -> str:
    """Simple text-only generation (no tools). Used by planner, executor, etc."""
    url, default_model = get_llm_settings()
    provider = get_llm_provider()
    m        = model or default_model

    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    # Check cache first
    from core.cache import cached_llm_call, cache_llm_result
    cached = cached_llm_call(messages, model=m)
    if cached is not None:
        log.debug("Cache hit for prompt: %s", prompt[:50])
        return cached.get("content", "")

    if _is_openai_compat(provider):
        endpoint = _openai_chat_url(url)
        headers  = _get_auth_headers(provider)
        payload  = {"model": m, "messages": messages, "stream": False, "max_tokens": 600}
        try:
            resp = requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            result = _parse_openai_response(resp.json())
            cache_llm_result(messages, result, model=m)
            return result["content"]
        except Exception as e:
            raise RuntimeError(f"OpenAI-compatible text call failed: {e}") from e

    # Native Ollama
    endpoint = f"{url}/api/chat"
    payload  = {"model": m, "messages": messages, "stream": False, "keep_alive": -1, "options": {"num_predict": 600}}

    try:
        resp = requests.post(endpoint, json=payload, timeout=timeout)
        resp.raise_for_status()
        result = _parse_ollama_response(resp.json())
        cache_llm_result(messages, result, model=m)
        return result["content"]
    except requests.exceptions.ConnectionError:
        if ensure_ollama_running():
            try:
                resp = requests.post(endpoint, json=payload, timeout=timeout)
                resp.raise_for_status()
                return _parse_ollama_response(resp.json())["content"]
            except Exception:
                pass
        raise RuntimeError(
            f"Cannot connect to Ollama at {url}. "
            "Make sure Ollama is installed and run: ollama serve"
        )
    except Exception as e:
        fb = _get_fallback_model()
        if fb and fb != m and _is_model_not_found(e):
            log.warning("Primary model '%s' not found — retrying with fallback '%s'", m, fb)
            payload["model"] = fb
            try:
                resp = requests.post(endpoint, json=payload, timeout=timeout)
                resp.raise_for_status()
                return _parse_ollama_response(resp.json())["content"]
            except Exception as fe:
                raise RuntimeError(f"LLM fallback also failed: {fe}") from fe
        raise RuntimeError(f"LLM text call failed: {e}") from e


# ---------------------------------------------------------------------------
# Streaming — shared SSE parser (used by Ollama Cloud + OpenAI-compatible)
# ---------------------------------------------------------------------------

def _stream_sse(
    url:      str,
    endpoint: str,
    payload:  dict,
    headers:  dict,
    timeout:  int,
    label:    str,
    error_msg: str,
) -> Generator[dict, None, None]:
    """
    Shared SSE streaming parser for OpenAI-compatible endpoints.

    Yields sentence events and a final 'done' event with content + tool_calls.
    """
    full_content = ""
    buf          = ""
    tc_fragments: dict[int, dict] = {}

    with requests.post(endpoint, json=payload, headers=headers, timeout=timeout, stream=True) as resp:
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
            delta  = choice.get("delta", {})
            text   = delta.get("content") or ""

            full_content += text
            buf          += text

            if text:
                yield {"type": "chunk", "text": text}

            # Yield complete sentences
            while True:
                m = _SENT_END.search(buf)
                if not m:
                    break
                sentence = buf[: m.start() + 1].strip()
                buf      = buf[m.end():]
                if sentence:
                    yield {"type": "sentence", "text": sentence}

            # Accumulate streaming tool-call fragments
            for tc in (delta.get("tool_calls") or []):
                idx = tc.get("index", 0)
                if idx not in tc_fragments:
                    tc_fragments[idx] = {"id": "", "function": {"name": "", "arguments": ""}}
                frag = tc_fragments[idx]
                frag["id"] = frag["id"] or tc.get("id", "")
                fn = tc.get("function", {})
                frag["function"]["name"]      += fn.get("name") or ""
                frag["function"]["arguments"] += fn.get("arguments") or ""

            finish = choice.get("finish_reason")
            if finish in ("stop", "tool_calls", "length"):
                break

    if buf.strip():
        yield {"type": "sentence", "text": buf.strip()}

    tool_calls = _parse_openai_tool_calls([
        {"id": frag["id"], "function": {"name": frag["function"]["name"], "arguments": frag["function"]["arguments"]}}
        for frag in (tc_fragments[idx] for idx in sorted(tc_fragments))
    ])

    yield {
        "type":       "done",
        "content":    full_content.strip(),
        "tool_calls": tool_calls,
    }


def call_llm_stream(
    messages: list,
    tools:    list | None = None,
    timeout:  int = 120,
    model:    str | None = None,
    max_tokens: int | None = None,
    provider: str | None = None,
    base_url: str | None = None,
) -> Generator[dict, None, None]:
    """
    Streaming chat request.  Routes to Ollama, Ollama Cloud, or OpenAI-compatible.

    Yields:
        {"type": "chunk", "text": str}             — each raw token delta
        {"type": "sentence", "text": str}          — each complete sentence
        {"type": "done", "content": str, "tool_calls": list}  — stream end
    """
    provider = provider or get_llm_provider()
    if base_url:
        url = base_url.rstrip("/")
        _, default_model = get_llm_settings()
    else:
        url, default_model = get_llm_settings()
    model    = model or default_model
    tok_cap  = max_tokens if max_tokens is not None else (
        _STREAM_MAX_TOKENS if tools else _STREAM_CHAT_TOKENS
    )

    if _is_openai_compat(provider):
        endpoint = _openai_chat_url(url)
        headers  = _get_auth_headers(provider)

        def _do_stream(model_name: str, use_tools: bool, token_limit: int) -> Generator[dict, None, None]:
            payload: dict = {
                "model": model_name, "messages": messages,
                "stream": True, "max_tokens": token_limit,
            }
            if use_tools and tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"
            yield from _stream_sse(url, endpoint, payload, headers, timeout, provider, provider)

        try:
            yield from _do_stream(model, bool(tools), tok_cap)
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            fb     = _get_fallback_model()

            # Rate limit → smaller fallback model, no tools
            if status == 429 and fb and fb != model:
                log.warning("Rate limited on '%s' — retrying with '%s'", model, fb)
                try:
                    yield from _do_stream(fb, False, _STREAM_CHAT_TOKENS)
                    return
                except Exception:
                    pass

            # Groq tool schema failure → retry without tools (same model)
            if tools and e.response is not None and _is_tool_use_error(e.response):
                log.warning("Tool call failed on %s — retrying without tools", provider)
                try:
                    yield from _do_stream(model, False, _STREAM_CHAT_TOKENS)
                    return
                except Exception:
                    pass

            raise RuntimeError(f"{provider} HTTP error: {status}") from e
        except requests.exceptions.ConnectionError:
            raise RuntimeError(f"Cannot reach {_provider_label(provider)} at {url}.")
        except requests.exceptions.Timeout:
            raise RuntimeError(f"{provider} stream timed out.")
        except Exception as e:
            raise RuntimeError(f"{provider} stream failed: {e}")
        return

    # Native Ollama — different wire format (JSON lines, not SSE)
    endpoint = f"{url}/api/chat"
    payload = {
        "model": model, "messages": messages,
        "stream": True, "keep_alive": -1,
        "options": {"num_predict": _STREAM_MAX_TOKENS, "num_gpu": 99},
    }
    if tools:
        payload["tools"] = tools

    def _do_native_stream() -> Generator[dict, None, None]:
        with requests.post(endpoint, json=payload, timeout=timeout, stream=True) as resp:
            resp.raise_for_status()
            full_content = ""
            tool_calls:  list = []
            buf          = ""

            for raw in resp.iter_lines():
                if not raw:
                    continue
                try:
                    chunk = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg   = chunk.get("message", {})
                delta = msg.get("content") or ""
                full_content += delta
                buf          += delta

                if delta:
                    yield {"type": "chunk", "text": delta}

                while True:
                    m = _SENT_END.search(buf)
                    if not m:
                        break
                    sentence = buf[: m.start() + 1].strip()
                    buf      = buf[m.end():]
                    if sentence:
                        yield {"type": "sentence", "text": sentence}

                tc = msg.get("tool_calls")
                if tc:
                    tool_calls.extend(tc)

                if chunk.get("done"):
                    if buf.strip():
                        yield {"type": "sentence", "text": buf.strip()}
                    yield {
                        "type":       "done",
                        "content":    full_content.strip(),
                        "tool_calls": tool_calls,
                    }
                    return

    try:
        yield from _do_native_stream()
    except requests.exceptions.ConnectionError as e:
        log.warning("Stream ConnectionError — trying to restart Ollama: %s", e)
        if ensure_ollama_running():
            yield from _do_native_stream()
            return
        raise RuntimeError(
            f"Cannot connect to Ollama at {url}. "
            "Make sure Ollama is installed and run: ollama serve"
        )
    except requests.exceptions.Timeout:
        raise RuntimeError("Ollama stream timed out.")
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"Ollama HTTP error: {e.response.status_code}")
    except Exception as e:
        log.error("Stream error: %s: %s", type(e).__name__, e)
        raise RuntimeError(f"LLM stream failed: {e}")
