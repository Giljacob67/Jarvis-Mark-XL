"""
MARK XL — Translator tool.

Translates text between languages using the LLM (no external API needed).

Actions:
    translate <text> — auto-detect source language and translate
    detect <text>    — detect the language of text
"""
from __future__ import annotations

from core.llm_client import call_llm_text
from core.logger import get_logger

log = get_logger("translator")


def translator_tool(
    parameters: dict,
    player=None,
    speak=None,
) -> str:
    params = parameters or {}
    action = params.get("action", "translate").lower()
    text = params.get("text", "")
    target = params.get("target_language", "English")
    source = params.get("source_language", "auto")

    if player:
        player.write_log(f"SYS: Translator — {action}")

    if not text:
        return "No text provided to translate."

    if action == "translate":
        prompt = (
            f"Translate the following text to {target}. "
            f"{'Auto-detect the source language. ' if source == 'auto' else f'The source language is {source}. '}"
            f"Reply with ONLY the translated text, nothing else.\n\n"
            f"Text: {text}"
        )
        try:
            result = call_llm_text(prompt)
            return f"Translation ({target}): {result}"
        except Exception as e:
            log.error("Translation failed: %s", e)
            return f"Translation failed: {e}"

    elif action == "detect":
        prompt = (
            "What language is this text written in? "
            "Reply with ONLY the language name in English.\n\n"
            f"Text: {text[:200]}"
        )
        try:
            result = call_llm_text(prompt)
            return f"Detected language: {result}"
        except Exception as e:
            return f"Detection failed: {e}"

    return f"Unknown translator action: {action}"
