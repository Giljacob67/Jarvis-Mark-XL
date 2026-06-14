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


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_llm_settings() -> tuple[str, str]:
    """Returns (base_url, model_name)."""
    cfg   = _load_config()
    url   = cfg.get("llm_url",   _DEFAULTS["llm_url"]).rstrip("/")
    model = cfg.get("llm_model", _DEFAULTS["llm_model"])
    return url, model


def get_llm_provider() -> str:
    """Returns 'ollama', 'ollama_cloud', or 'openai'."""
    raw = _load_config().get("llm_provider", "ollama").strip().lower()
    if raw in ("openai", "lmstudio", "localai", "jan", "llamacpp"):
        return "openai"
    if raw in ("ollama_cloud", "ollamacloud"):
        return "ollama_cloud"
    return "ollama"


def _get_fallback_model() -> str:
    return _load_config().get("llm_fallback_model", "").strip()


def _is_model_not_found(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in ("not found", "pull the model", "model", "404", "doesn't exist"))


def _get_auth_headers() -> dict:
    """Bearer token for Ollama Cloud API."""
    api_key = _load_config().get("ollama_api_key", "") or os.environ.get("OLLAMA_API_KEY", "")
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


# ---------------------------------------------------------------------------
# Ollama lifecycle
# ---------------------------------------------------------------------------

def ensure_ollama_running(timeout: int = 15) -> bool:
    """Ping or auto-launch Ollama.  Returns True if reachable."""
    url, _   = get_llm_settings()
    provider = get_llm_provider()

    if provider in ("ollama_cloud", "openai"):
        health = f"{url}/v1/models"
        headers = _get_auth_headers() if provider == "ollama_cloud" else {}
        try:
            ok = requests.get(health, headers=headers, timeout=5).status_code == 200
            label = "Ollama Cloud API" if provider == "ollama_cloud" else "OpenAI-compatible server"
            log.info("%s %s at %s", label, "reachable" if ok else "returned non-200", url)
            return ok
        except Exception as e:
            log.warning("Cannot reach %s at %s: %s", label, url, e)
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

    if provider in ("ollama_cloud", "openai"):
        payload: dict = {
            "model": model, "messages": messages,
            "stream": False, "max_tokens": 1,
        }
        headers = _get_auth_headers() if provider == "ollama_cloud" else {}
        try:
            resp = requests.post(f"{url}/v1/chat/completions", json=payload, headers=headers, timeout=180)
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
    endpoint   = f"{url}/v1/chat/completions" if provider != "ollama" else f"{url}/api/chat"
    headers    = _get_auth_headers() if provider == "ollama_cloud" else {}

    if provider in ("ollama_cloud", "openai"):
        payload: dict = {
            "model": model, "messages": messages,
            "stream": False, "max_tokens": 150,
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
        "options": {"num_predict": 500, "num_gpu": 99},
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

    if provider in ("ollama_cloud", "openai"):
        endpoint = f"{url}/v1/chat/completions"
        headers  = _get_auth_headers() if provider == "ollama_cloud" else {}
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
) -> Generator[dict, None, None]:
    """
    Streaming chat request.  Routes to Ollama, Ollama Cloud, or OpenAI-compatible.

    Yields:
        {"type": "sentence", "text": str}          — each complete sentence
        {"type": "done", "content": str, "tool_calls": list}  — stream end
    """
    url, model = get_llm_settings()
    provider   = get_llm_provider()

    if provider in ("ollama_cloud", "openai"):
        endpoint = f"{url}/v1/chat/completions"
        headers  = _get_auth_headers() if provider == "ollama_cloud" else {}
        payload: dict = {
            "model": model, "messages": messages,
            "stream": True, "max_tokens": 150,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            yield from _stream_sse(url, endpoint, payload, headers, timeout, provider, provider)
        except requests.exceptions.ConnectionError:
            label = "Ollama Cloud API" if provider == "ollama_cloud" else "OpenAI-compatible server"
            raise RuntimeError(f"Cannot reach {label} at {url}.")
        except requests.exceptions.Timeout:
            raise RuntimeError(f"{provider} stream timed out.")
        except requests.exceptions.HTTPError as e:
            raise RuntimeError(f"{provider} HTTP error: {e.response.status_code}")
        except Exception as e:
            raise RuntimeError(f"{provider} stream failed: {e}")
        return

    # Native Ollama — different wire format (JSON lines, not SSE)
    endpoint = f"{url}/api/chat"
    payload = {
        "model": model, "messages": messages,
        "stream": True, "keep_alive": -1,
        "options": {"num_predict": 500, "num_gpu": 99},
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
