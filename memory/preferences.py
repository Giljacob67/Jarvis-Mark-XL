"""
MARK XL — Preference Learning.

Learns user preferences from conversation history and interactions.
Stores learned preferences in memory for context-aware responses.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from core.logger import get_logger
from core.paths import BASE_DIR

log = get_logger("preferences")

PREFERENCES_PATH = BASE_DIR / "memory" / "learned_preferences.json"


def _load_preferences() -> dict:
    try:
        return json.loads(PREFERENCES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_preferences(prefs: dict) -> None:
    PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREFERENCES_PATH.write_text(json.dumps(prefs, indent=2, ensure_ascii=False), encoding="utf-8")


# Patterns that indicate preferences
_PREFERENCE_PATTERNS = [
    (r"(?:eu )?(?:gosto|curto|adoro|prefiro)\s+(?:de\s+)?(.+)", "likes"),
    (r"(?:eu )?(?:não|nao)\s+(?:gosto|curto|adoro|prefiro)\s+(?:de\s+)?(.+)", "dislikes"),
    (r"(?:minha|minhas?)\s+(?:favorit[aoe]|preferid[aoe])\s+(?:é|são|e)\s+(.+)", "favorite"),
    (r"(?:eu )?(?:sempre|normalmente|costumo)\s+(.+)", "habit"),
    (r"(?:não|nao)\s+(?:sempre|normalmente|costumo)\s+(.+)", "anti_habit"),
    (r"(?:lembre[- ]?(?:me|mo))\s+(?:que\s+)?(.+)", "instruction"),
    (r"(?:a partir\s+de\s+agora|sempre)\s*,?\s*(?:faça|faz|coloque|manda|envie)\s+(.+)", "instruction"),
]


def extract_preferences_from_text(text: str) -> list[dict]:
    """Extract preferences from user text using pattern matching."""
    text_lower = text.lower().strip()
    extracted = []

    for pattern, pref_type in _PREFERENCE_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            value = match.group(1).strip().rstrip(".,;:!")
            if len(value) > 3:  # Ignore very short matches
                extracted.append({
                    "type": pref_type,
                    "value": value,
                    "source_text": text[:200],
                    "learned_at": datetime.now().isoformat(),
                })

    return extracted


def learn_preference(category: str, key: str, value: str) -> str:
    """Manually store a preference."""
    prefs = _load_preferences()
    if category not in prefs:
        prefs[category] = {}
    prefs[category][key] = {
        "value": value,
        "learned_at": datetime.now().isoformat(),
    }
    _save_preferences(prefs)
    return f"Learned: {category}/{key} = {value}"


def get_preferences(category: str | None = None) -> dict:
    """Get stored preferences."""
    prefs = _load_preferences()
    if category:
        return prefs.get(category, {})
    return prefs


def format_preferences_for_prompt() -> str:
    """Format preferences for inclusion in the system prompt."""
    prefs = _load_preferences()
    if not prefs:
        return ""

    parts = ["[LEARNED PREFERENCES]"]
    for category, entries in prefs.items():
        if isinstance(entries, dict):
            for key, entry in entries.items():
                value = entry.get("value", str(entry)) if isinstance(entry, dict) else str(entry)
                parts.append(f"  {category}/{key}: {value}")

    return "\n".join(parts) if len(parts) > 1 else ""


def auto_learn_from_conversation(messages: list[dict]) -> int:
    """
    Automatically extract and store preferences from conversation messages.
    Returns the number of preferences learned.
    """
    count = 0
    prefs = _load_preferences()

    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue

        extracted = extract_preferences_from_text(content)
        for pref in extracted:
            category = pref["type"]
            value = pref["value"]
            if category not in prefs:
                prefs[category] = {}

            # Generate a key from the value
            key = value[:50].replace(" ", "_").replace("/", "_")

            # Don't overwrite if already exists
            if key not in prefs[category]:
                prefs[category][key] = {
                    "value": value,
                    "learned_at": pref["learned_at"],
                    "source": pref["source_text"][:100],
                }
                count += 1

    if count > 0:
        _save_preferences(prefs)
        log.info("Auto-learned %d preferences from conversation", count)

    return count
