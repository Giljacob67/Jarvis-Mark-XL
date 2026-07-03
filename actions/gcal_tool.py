"""
MARK XL — Google Calendar provider (calendar.events scope).

Consumed by actions/calendar_tool.py (list/create routing) and by the
proactive engine via calendar_tool.agenda_upcoming() (merged with the
local JSON agenda).  All-day events carry all_day=True so the engine can
skip time-based reminders for them.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from core.google_auth import get_credentials, google_ready  # noqa: F401  (re-exported)
from core.logger import get_logger

log = get_logger("gcal")


def _service():
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=get_credentials(), cache_discovery=False)


def parse_gcal_event(ev: dict) -> dict | None:
    """Google event dict → {'title', 'start': datetime, 'all_day': bool} | None."""
    start = ev.get("start", {})
    title = ev.get("summary", "Evento")
    if "dateTime" in start:
        try:
            dt = datetime.fromisoformat(start["dateTime"])
            return {"title": title, "start": dt.replace(tzinfo=None), "all_day": False}
        except ValueError:
            return None
    if "date" in start:                        # all-day event
        try:
            dt = datetime.strptime(start["date"], "%Y-%m-%d")
            return {"title": title, "start": dt, "all_day": True}
        except ValueError:
            return None
    return None


def gcal_upcoming(hours: int = 48) -> list[dict]:
    """Structured upcoming events from the primary calendar."""
    now = datetime.now().astimezone()
    resp = _service().events().list(
        calendarId="primary",
        timeMin=now.isoformat(),
        timeMax=(now + timedelta(hours=hours)).isoformat(),
        singleEvents=True,
        orderBy="startTime",
        maxResults=25,
    ).execute()
    out = []
    for ev in resp.get("items", []):
        parsed = parse_gcal_event(ev)
        if parsed:
            out.append(parsed)
    return out


def gcal_list_speakable(days: int = 7) -> str:
    """Voice-friendly pt-BR listing of upcoming events."""
    events = gcal_upcoming(hours=days * 24)
    if not events:
        return "Nenhum compromisso no Google Calendar."
    now = datetime.now()
    lines = []
    for ev in events[:10]:
        dt = ev["start"]
        day = "hoje" if dt.date() == now.date() else (
            "amanhã" if dt.date() == (now + timedelta(days=1)).date()
            else dt.strftime("%d/%m")
        )
        when = f"{day}, o dia todo" if ev["all_day"] else f"{day} às {dt.strftime('%H:%M')}"
        lines.append(f"{ev['title']}, {when}")
    plural = "compromissos" if len(events) > 1 else "compromisso"
    return f"{len(events)} {plural}: " + "; ".join(lines) + "."


def gcal_create(title: str, start: datetime, duration_min: int = 60) -> str:
    start_l = start.astimezone()
    end_l   = start_l + timedelta(minutes=duration_min)
    _service().events().insert(calendarId="primary", body={
        "summary": title,
        "start": {"dateTime": start_l.isoformat()},
        "end":   {"dateTime": end_l.isoformat()},
    }).execute()
    return f"Agendado no Google Calendar: {title}, {start.strftime('%d/%m às %H:%M')}."
