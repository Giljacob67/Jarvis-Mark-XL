"""
MARK XL — Proactive Suggestion Engine.

Analyzes context (time, location, preferences, recent activity) to
provide proactive suggestions and recommendations.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from core.logger import get_logger
from core.paths import BASE_DIR

log = get_logger("suggestions")


def get_time_suggestions() -> list[str]:
    """Generate suggestions based on time of day."""
    now = datetime.now()
    hour = now.hour
    suggestions = []

    if 6 <= hour < 9:
        suggestions.extend([
            "Good morning! Want me to check the weather?",
            "Want me to set up your morning routine?",
        ])
    elif 12 <= hour < 14:
        suggestions.extend([
            "Lunch time! Want me to set a reminder?",
        ])
    elif 17 <= hour < 20:
        suggestions.extend([
            "Evening! Want me to check tomorrow's schedule?",
            "Want me to set up evening routines?",
        ])
    elif 22 <= hour or hour < 2:
        suggestions.extend([
            "Late night? Want me to set a sleep reminder?",
        ])

    return suggestions


def get_context_suggestions(context: dict) -> list[str]:
    """Generate suggestions based on current context."""
    suggestions = []
    prefs = context.get("preferences", {})
    location = context.get("location", {})
    calendar = context.get("upcoming_events", [])

    # Based on preferences
    if prefs.get("likes"):
        for like in list(prefs["likes"].values())[:2]:
            value = like.get("value", str(like)) if isinstance(like, dict) else str(like)
            suggestions.append(f"I know you like {value}. Want me to find related content?")

    # Based on location
    city = location.get("city", "")
    if city and city != "Unknown":
        suggestions.append(f"Located in {city}. Want local weather or events?")

    # Based on calendar
    if calendar:
        next_event = calendar[0]
        suggestions.append(f"Next event: {next_event.get('title', 'Unknown')} at {next_event.get('time', '?')}")

    return suggestions


def get_suggestions(context: dict | None = None) -> list[str]:
    """Get all proactive suggestions based on context."""
    suggestions = get_time_suggestions()
    if context:
        suggestions.extend(get_context_suggestions(context))
    return suggestions[:5]  # Max 5 suggestions


def format_suggestions_for_prompt(context: dict | None = None) -> str:
    """Format suggestions for inclusion in the system prompt."""
    suggestions = get_suggestions(context)
    if not suggestions:
        return ""
    parts = ["[PROACTIVE SUGGESTIONS — mention if relevant]"]
    for s in suggestions:
        parts.append(f"  - {s}")
    return "\n".join(parts)
