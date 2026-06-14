"""
MARK XL — Timer tool.

Set countdown timers with spoken alerts.

Actions:
    start <seconds> [label] — start a timer
    list                    — list active timers
    cancel <id>             — cancel a timer
"""
from __future__ import annotations

import threading
import time

from core.logger import get_logger

log = get_logger("timer")

_timers: dict[int, dict] = {}
_next_id = 1
_lock = threading.Lock()


def _timer_thread(timer_id: int, seconds: int, label: str, speak) -> None:
    time.sleep(seconds)
    with _lock:
        if timer_id in _timers:
            _timers[timer_id]["done"] = True
    msg = f"Timer finished: {label}" if label else "Timer finished!"
    log.info("Timer %d done: %s", timer_id, label)
    if speak:
        speak(msg)


def timer_tool(
    parameters: dict,
    player=None,
    speak=None,
) -> str:
    global _next_id
    params = parameters or {}
    action = params.get("action", "start").lower()

    if player:
        player.write_log(f"SYS: Timer — {action}")

    if action == "start":
        seconds = int(params.get("seconds", 60))
        label = params.get("label", "")
        if seconds <= 0:
            return "Invalid duration."

        with _lock:
            timer_id = _next_id
            _next_id += 1
            _timers[timer_id] = {"seconds": seconds, "label": label, "done": False}

        t = threading.Thread(
            target=_timer_thread,
            args=(timer_id, seconds, label, speak),
            daemon=True,
        )
        t.start()

        mins, secs = divmod(seconds, 60)
        time_str = f"{mins}m {secs}s" if mins else f"{secs}s"
        return f"Timer #{timer_id} set for {time_str}{' — ' + label if label else ''}."

    elif action == "list":
        with _lock:
            active = {k: v for k, v in _timers.items() if not v["done"]}
        if not active:
            return "No active timers."
        lines = []
        for tid, t in active.items():
            label = f" — {t['label']}" if t["label"] else ""
            lines.append(f"  #{tid}: {t['seconds']}s{label}")
        return f"Active timers:\n" + "\n".join(lines)

    elif action == "cancel":
        timer_id = int(params.get("id", 0))
        with _lock:
            if timer_id in _timers:
                del _timers[timer_id]
                return f"Timer #{timer_id} cancelled."
        return f"Timer #{timer_id} not found."

    return f"Unknown timer action: {action}"
