"""Calendar tool — local calendar events (macOS/Windows/Linux), no OAuth required."""
import platform
import subprocess
import sys
from datetime import datetime

_OS = platform.system()


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
        return "No calendar tool found. Install 'khal' (pip install khal) or 'icalBuddy'."


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
        return "Install 'khal' for calendar support on Linux."

    return "Calendar creation not supported on Windows without Outlook COM."


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
