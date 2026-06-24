"""
Shared vision helpers — screen capture analysis and image file analysis.
Uses Ollama / OpenAI-compatible vision endpoints configured in api_keys.json.
"""
from __future__ import annotations

import base64
import json

import requests

from core.paths import API_CONFIG_PATH
from core.logger import get_logger

log = get_logger("vision")

_SYSTEM_PROMPT = (
    "You are JARVIS, an advanced AI assistant. "
    "Analyze the provided image with precision and intelligence. "
    "Be concise and direct — maximum two sentences unless the question "
    "requires more detail. Address the user respectfully."
)


def _load_config() -> dict:
    try:
        return json.loads(API_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def analyze_image(image_bytes: bytes, user_text: str, mime: str = "image/jpeg") -> str:
    """
    Send image bytes to the configured vision model.
    Returns analysis text or an error message string.
    """
    cfg = _load_config()
    url = cfg.get("llm_url", "http://localhost:11434").rstrip("/")
    provider = cfg.get("llm_provider", "ollama").strip().lower()
    vision_model = cfg.get("vision_model") or cfg.get("llm_model", "llava")
    b64 = base64.b64encode(image_bytes).decode("ascii")

    if provider in ("ollama_cloud", "openai", "groq", "lmstudio", "localai", "jan", "llamacpp"):
        from core.llm_client import _openai_chat_url
        endpoint = _openai_chat_url(url)
        headers: dict = {}
        api_key = (
            cfg.get("ollama_api_key", "")
            or cfg.get("groq_api_key", "")
            or cfg.get("openai_api_key", "")
        )
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": vision_model,
            "stream": False,
            "max_tokens": 600,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    ],
                },
            ],
        }
        try:
            resp = requests.post(endpoint, json=payload, headers=headers, timeout=90)
            resp.raise_for_status()
            data = resp.json()
            return (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
        except requests.exceptions.ConnectionError:
            return "Cannot reach the vision API. Check llm_url and API key in config."
        except Exception as e:
            log.error("Vision API error: %s", e)
            return f"Vision analysis failed: {e}"

    # Native Ollama
    payload = {
        "model": vision_model,
        "stream": False,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_text, "images": [b64]},
        ],
    }
    try:
        resp = requests.post(f"{url}/api/chat", json=payload, timeout=90)
        resp.raise_for_status()
        return (resp.json().get("message", {}).get("content") or "").strip()
    except requests.exceptions.ConnectionError:
        return "Cannot connect to Ollama. Make sure Ollama is running."
    except Exception as e:
        log.error("Ollama vision error: %s", e)
        return f"Vision analysis failed: {e}"