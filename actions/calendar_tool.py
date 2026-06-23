"""
MARK XL — Calendar Integration.

Read, create, and manage calendar events using the local filesystem.
Supports ICS import and basic event management.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from core.logger import get_logger
from core.paths import BASE_DIR

log = get_logger("calendar")

CALENDAR_PATH = BASE_DIR / "config" / "calendar.json"


def _load_events() -> list[dict]:
    try:
        return json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_events(events: list[dict]) -> None:
    CALENDAR_PATH.parent.mkdir(parents=True, exist_ok=True)
    CALENDAR_PATH.write_text(json.dumps(events, indent=2, ensure_ascii=False), encoding="utf-8")


def get_upcoming(hours: int = 24) -> list[dict]:
    """Get events in the next N hours."""
    now = datetime.now()
    cutoff = now + timedelta(hours=hours)
    events = _load_events()
    upcoming = []
    for e in events:
        try:
            event_time = datetime.fromisoformat(e.get("time", ""))
            if now <= event_time <= cutoff:
                upcoming.append(e)
        except (ValueError, TypeError):
            pass
    return sorted(upcoming, key=lambda e: e.get("time", ""))


def add_event(title: str, time_str: str, description: str = "", duration_min: int = 60) -> str:
    """Add a calendar event."""
    events = _load_events()
    event = {
        "id": f"evt_{int(datetime.now().timestamp())}",
        "title": title,
        "time": time_str,
        "description": description,
        "duration_min": duration_min,
        "created_at": datetime.now().isoformat(),
    }
    events.append(event)
    _save_events(events)
    return f"Event '{title}' scheduled for {time_str}."


def remove_event(event_id: str) -> str:
    """Remove an event by ID."""
    events = _load_events()
    for e in events:
        if e.get("id") == event_id or e.get("title", "").lower() == event_id.lower():
            events.remove(e)
            _save_events(events)
            return f"Event '{e['title']}' removed."
    return f"Event '{event_id}' not found."


def list_events(limit: int = 10) -> list[dict]:
    """List upcoming events."""
    events = _load_events()
    now = datetime.now()
    future = [e for e in events if _parse_time(e.get("time", "")) >= now]
    return sorted(future, key=lambda e: e.get("time", ""))[:limit]


def _parse_time(time_str: str) -> datetime:
    try:
        return datetime.fromisoformat(time_str)
    except (ValueError, TypeError):
        return datetime.min
