"""Calendar tool — local calendar events (macOS/Windows/Linux), no OAuth required.

On Linux without khal (and as a universal fallback), events live in a local
JSON agenda (memory/agenda.json).  The proactive engine reads the same file
for morning briefings and event reminders.  When Google Calendar integration
lands, it becomes another provider behind the same interface.
"""
import json
import platform
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

_OS = platform.system()

_AGENDA_PATH = Path(__file__).resolve().parent.parent / "memory" / "agenda.json"


# ---------------------------------------------------------------------------
# Local JSON agenda (fallback provider — also feeds the proactive engine)
# ---------------------------------------------------------------------------

def _load_agenda() -> list[dict]:
    try:
        data = json.loads(_AGENDA_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_agenda(events: list[dict]) -> None:
    _AGENDA_PATH.parent.mkdir(parents=True, exist_ok=True)
    _AGENDA_PATH.write_text(
        json.dumps(events, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def parse_datetime_br(text: str) -> datetime | None:
    """Parse pt-BR-friendly datetime strings the LLM is likely to pass.

    Accepts: 'YYYY-MM-DD HH:MM', 'DD/MM/YYYY HH:MM', 'DD/MM HH:MM',
    'hoje HH:MM', 'amanhã HH:MM' (and 'amanha').  Returns None if unparsable.
    """
    t = (text or "").strip().lower()
    if not t:
        return None
    now = datetime.now()

    m = re.match(r"^(hoje|amanh[aã])\s+(\d{1,2})[:h](\d{2})?$", t)
    if m:
        base = now if m.group(1) == "hoje" else now + timedelta(days=1)
        return base.replace(hour=int(m.group(2)), minute=int(m.group(3) or 0),
                            second=0, microsecond=0)

    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%d/%m/%Y %H:%M", "%d/%m %H:%M"):
        try:
            dt = datetime.strptime(t, fmt)
            if fmt == "%d/%m %H:%M":                     # no year → next occurrence
                dt = dt.replace(year=now.year)
                if dt < now - timedelta(days=1):
                    dt = dt.replace(year=now.year + 1)
            return dt
        except ValueError:
            continue
    return None


def agenda_upcoming(hours: int = 48) -> list[dict]:
    """Structured upcoming events for the proactive engine:
    [{'title', 'start' (datetime)}...] sorted by start."""
    now = datetime.now()
    horizon = now + timedelta(hours=hours)
    out = []
    for ev in _load_agenda():
        dt = parse_datetime_br(ev.get("start", ""))
        if dt and now - timedelta(minutes=5) <= dt <= horizon:
            out.append({"title": ev.get("title", "Evento"), "start": dt})
    return sorted(out, key=lambda e: e["start"])


def _agenda_list(days: int) -> str:
    now     = datetime.now()
    horizon = now + timedelta(days=days)
    events = []
    for ev in _load_agenda():
        dt = parse_datetime_br(ev.get("start", ""))
        if dt and dt >= now - timedelta(hours=1) and dt <= horizon:
            events.append((dt, ev.get("title", "Evento")))
    if not events:
        return "Nenhum compromisso agendado."
    events.sort()
    lines = []
    for dt, title in events[:10]:
        day = "hoje" if dt.date() == now.date() else (
            "amanhã" if dt.date() == (now + timedelta(days=1)).date()
            else dt.strftime("%d/%m")
        )
        lines.append(f"{title}, {day} às {dt.strftime('%H:%M')}")
    plural = "compromissos" if len(events) > 1 else "compromisso"
    return f"{len(events)} {plural}: " + "; ".join(lines) + "."


def _agenda_create(title: str, start: str) -> str:
    dt = parse_datetime_br(start)
    if dt is None:
        return ("Não entendi a data. Use por exemplo 'amanhã 10:00', "
                "'05/07 14:30' ou '2026-07-10 09:00'.")
    events = _load_agenda()
    events.append({"title": title, "start": dt.strftime("%Y-%m-%d %H:%M")})
    _save_agenda(events)
    return f"Agendado: {title}, {dt.strftime('%d/%m às %H:%M')}."


def _run(cmd: list[str], timeout: int = 10) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or r.stderr).strip()
    except subprocess.TimeoutExpired:
        return -1, "Command timed out."
    except FileNotFoundError:
        return -1, f"Command not found: {cmd[0]}"


def _list_events(params: dict) -> str:
    days = int(params.get("days", 7))

    if _OS == "Darwin":
        # icalBuddy lists events from all local calendars
        if subprocess.run(["which", "icalBuddy"], capture_output=True).returncode == 0:
            code, out = _run(["icalBuddy", f"eventsFrom:today to:{days}d"])
            return out or "No events found, sir."
        # fallback: AppleScript
        script = (
            'tell application "Calendar"\n'
            '  set ev to every event of every calendar\n'
            '  set out to ""\n'
            '  repeat with e in ev\n'
            '    set out to out & summary of e & " @ " & start date of e & "\n"\n'
            '  end repeat\n'
            '  return out\n'
            'end tell'
        )
        code, out = _run(["osascript", "-e", script])
        return out or "No events, sir."

    elif _OS == "Windows":
        ps = (
            "Add-Type -AssemblyName 'Microsoft.Office.Interop.Outlook' 2>$null; "
            "$ol = New-Object -ComObject Outlook.Application; "
            "$ns = $ol.GetNamespace('MAPI'); "
            "$cal = $ns.GetDefaultFolder(9); "
            "$now = [DateTime]::Now; "
            "$end = $now.AddDays(" + str(days) + "); "
            "$items = $cal.Items; "
            "$items.IncludeRecurrences = $true; "
            "$items.Sort('[Start]'); "
            "$filtered = $items.Restrict('[Start] >= \"' + $now.ToString('g') + '\" AND [Start] <= \"' + $end.ToString('g') + '\"'); "
            "foreach ($e in $filtered) { Write-Output ($e.Subject + ' @ ' + $e.Start) }"
        )
        code, out = _run(["powershell", "-Command", ps], timeout=15)
        return out or "No events found, sir."

    else:  # Linux
        if subprocess.run(["which", "khal"], capture_output=True).returncode == 0:
            code, out = _run(["khal", "list", "today", f"{days}d"])
            return out or "No events, sir."
        # No khal → local JSON agenda (also read by the proactive engine)
        return _agenda_list(days)


def _create_event(params: dict) -> str:
    title    = params.get("title", "Event")
    start    = params.get("start", "")
    end      = params.get("end", "")
    calendar = params.get("calendar", "")

    if not start:
        return "Please provide a start date/time, sir."

    if _OS == "Darwin":
        cal_clause = f'calendar "{calendar}"' if calendar else "first calendar"
        end_clause = f'end date:date "{end}"' if end else ""
        script = (
            f'tell application "Calendar"\n'
            f'  tell {cal_clause}\n'
            f'    make new event at end of events with properties '
            f'{{summary:"{title}", start date:date "{start}" {end_clause}}}\n'
            f'  end tell\n'
            f'end tell'
        )
        code, out = _run(["osascript", "-e", script])
        if code == 0:
            return f"Event '{title}' created, sir."
        return f"Failed to create event: {out}"

    elif _OS == "Linux":
        if subprocess.run(["which", "khal"], capture_output=True).returncode == 0:
            cmd = ["khal", "new", start]
            if end:
                cmd += [end]
            cmd.append(title)
            code, out = _run(cmd)
            return f"Event '{title}' created, sir." if code == 0 else f"Failed: {out}"
        # No khal → local JSON agenda
        return _agenda_create(title, start)

    return _agenda_create(title, start)   # Windows without Outlook → local agenda


def calendar_tool(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    action = params.get("action", "list").lower()

    if player:
        player.write_log(f"[Calendar] {action}")

    if action == "list":
        return _list_events(params)
    elif action in ("create", "add"):
        return _create_event(params)

    return f"Unknown calendar action: {action}. Use list or create."
