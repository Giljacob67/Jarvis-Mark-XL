"""
MARK XL — Google Calendar integration via CalDAV (no OAuth required).

Uses a CalDAV URL to read/write calendar events.
Config in api_keys.json:
    "caldav_url": "https://caldav.icloud.com/...",
    "caldav_user": "your@email.com",
    "caldav_password": "app-password",

Alternative: use the built-in calendar_tool.py for local calendar.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

from core.paths import API_CONFIG_PATH
from core.logger import get_logger

log = get_logger("caldav")


def _load_config() -> dict:
    try:
        return json.loads(API_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _make_request(method: str, url: str, data: str | None = None,
                  headers: dict | None = None) -> tuple[int, str]:
    """Make an HTTP request to CalDAV server."""
    cfg = _load_config()
    user = cfg.get("caldav_user", "")
    password = cfg.get("caldav_password", "")

    import requests
    try:
        resp = requests.request(
            method, url,
            auth=(user, password) if user else None,
            headers=headers or {},
            data=data,
            timeout=15,
        )
        return resp.status_code, resp.text
    except Exception as e:
        log.error("CalDAV error: %s", e)
        return 0, str(e)


def get_events(days: int = 7) -> list[dict]:
    """Get calendar events for the next N days."""
    cfg = _load_config()
    caldav_url = cfg.get("caldav_url", "")
    if not caldav_url:
        return []

    now = datetime.utcnow()
    end = now + timedelta(days=days)

    # Build REPORT request body
    body = f"""<?xml version="1.0" encoding="utf-8"?>
    <C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
      <D:prop>
        <D:getetag/>
        <C:calendar-data/>
      </D:prop>
      <C:filter>
        <C:comp-filter name="VCALENDAR">
          <C:comp-filter name="VEVENT">
            <C:time-range start="{now.strftime('%Y%m%dT%H%M%SZ')}"
                          end="{end.strftime('%Y%m%dT%H%M%SZ')}"/>
          </C:comp-filter>
        </C:comp-filter>
      </C:filter>
    </C:calendar-query>"""

    headers = {
        "Content-Type": "application/xml; charset=utf-8",
        "Depth": "1",
    }

    status, text = _make_request("REPORT", caldav_url, body, headers)
    if status not in (200, 207):
        return []

    # Parse iCal events from response
    events = []
    try:
        # Simple parsing — extract VEVENT blocks
        import re
        vevents = re.findall(r'BEGIN:VEVENT(.*?)END:VEVENT', text, re.DOTALL)
        for vevent in vevents:
            event = {}
            for line in vevent.strip().split('\n'):
                line = line.strip()
                if line.startswith('SUMMARY:'):
                    event['summary'] = line[8:]
                elif line.startswith('DTSTART'):
                    event['start'] = line.split(':', 1)[-1] if ':' in line else ''
                elif line.startswith('DTEND'):
                    event['end'] = line.split(':', 1)[-1] if ':' in line else ''
                elif line.startswith('DESCRIPTION:'):
                    event['description'] = line[12:]
            if event.get('summary'):
                events.append(event)
    except Exception as e:
        log.error("Failed to parse CalDAV response: %s", e)

    return events


def create_event(summary: str, start: str, end: str | None = None,
                 description: str = "") -> bool:
    """Create a calendar event."""
    cfg = _load_config()
    caldav_url = cfg.get("caldav_url", "")
    if not caldav_url:
        return False

    if not end:
        # Default: 1 hour after start
        try:
            start_dt = datetime.fromisoformat(start)
            end_dt = start_dt + timedelta(hours=1)
            end = end_dt.isoformat()
        except Exception:
            end = start

    # Build iCal event
    uid = f"jarvis-{int(time.time())}@jarvis"
    ical = f"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:{uid}
SUMMARY:{summary}
DTSTART:{start}
DTEND:{end}
DESCRIPTION:{description}
END:VEVENT
END:VCALENDAR"""

    body = f"""<?xml version="1.0" encoding="utf-8"?>
    <C:mkcalendar xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
      <D:set>
        <D:prop>
          <C:calendar-data>{ical}</C:calendar-data>
        </D:prop>
      </D:set>
    </C:mkcalendar>"""

    headers = {"Content-Type": "application/xml; charset=utf-8"}
    status, _ = _make_request("PUT", f"{caldav_url}/{uid}.ics", body, headers)
    return status in (200, 201, 204)
