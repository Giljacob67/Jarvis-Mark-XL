"""
MARK XL — Routine Automation Engine.

Time/event-based automation using APScheduler.
Create, list, enable/disable, and delete routines that trigger actions.

Routine types:
  - cron: Schedule based on time/day (e.g., "turn on lights at 7pm every weekday")
  - interval: Repeat every N minutes/hours
  - once: One-shot timer (e.g., "remind me in 30 minutes")
  - event: Triggered by system events (e.g., "when phone connects, announce it")
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from core.logger import get_logger
from core.paths import BASE_DIR

log = get_logger("routines")

ROUTINES_PATH = BASE_DIR / "config" / "routines.json"
_lock = threading.Lock()
_scheduler = None


def _load_routines() -> list[dict]:
    try:
        return json.loads(ROUTINES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_routines(routines: list[dict]) -> None:
    ROUTINES_PATH.parent.mkdir(parents=True, exist_ok=True)
    ROUTINES_PATH.write_text(json.dumps(routines, indent=2, ensure_ascii=False), encoding="utf-8")


def _get_scheduler():
    global _scheduler
    if _scheduler is None:
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            _scheduler = BackgroundScheduler(daemon=True)
            _scheduler.start()
        except Exception as e:
            log.error("Failed to start scheduler: %s", e)
    return _scheduler


def init_routines(execute_fn: Callable[[str], None]) -> None:
    """
    Initialize all saved routines and start the scheduler.
    execute_fn receives a command string to execute (e.g., "turn on the kitchen lights").
    """
    scheduler = _get_scheduler()
    if scheduler is None:
        return

    routines = _load_routines()
    loaded = 0
    for routine in routines:
        if not routine.get("enabled", True):
            continue
        try:
            _add_job(scheduler, routine, execute_fn)
            loaded += 1
        except Exception as e:
            log.warning("Failed to load routine '%s': %s", routine.get("name", "?"), e)

    log.info("Loaded %d/%d routines", loaded, len(routines))


def _add_job(scheduler, routine: dict, execute_fn: Callable) -> None:
    """Add a single routine job to the scheduler."""
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    job_id = routine.get("id", routine.get("name", str(time.time())))
    routine_type = routine.get("type", "cron")
    command = routine.get("command", "")
    name = routine.get("name", "Unnamed")

    def _run():
        log.info("Executing routine '%s': %s", name, command)
        try:
            execute_fn(command)
        except Exception as e:
            log.error("Routine '%s' failed: %s", name, e)

    if routine_type == "cron":
        schedule = routine.get("schedule", {})
        trigger = CronTrigger(
            hour=schedule.get("hour", "*"),
            minute=schedule.get("minute", "0"),
            day_of_week=schedule.get("day_of_week", "*"),
            day=schedule.get("day", "*"),
            month=schedule.get("month", "*"),
        )
        scheduler.add_job(_run, trigger, id=job_id, name=name, replace_existing=True)

    elif routine_type == "interval":
        interval = routine.get("interval", {})
        trigger = IntervalTrigger(
            minutes=interval.get("minutes", 60),
            hours=interval.get("hours", 0),
        )
        scheduler.add_job(_run, trigger, id=job_id, name=name, replace_existing=True)

    elif routine_type == "once":
        run_at = routine.get("run_at")
        if run_at:
            run_date = datetime.fromisoformat(run_at)
            scheduler.add_job(_run, 'date', run_date=run_date, id=job_id, name=name)


def add_routine(
    name: str,
    command: str,
    routine_type: str = "cron",
    schedule: dict | None = None,
    interval: dict | None = None,
    run_at: str | None = None,
    enabled: bool = True,
    execute_fn: Callable | None = None,
) -> str:
    """Add a new routine and save it."""
    routine = {
        "id": f"r_{int(time.time())}_{name[:20]}",
        "name": name,
        "command": command,
        "type": routine_type,
        "enabled": enabled,
        "created_at": datetime.now().isoformat(),
    }
    if schedule:
        routine["schedule"] = schedule
    if interval:
        routine["interval"] = interval
    if run_at:
        routine["run_at"] = run_at

    with _lock:
        routines = _load_routines()
        routines.append(routine)
        _save_routines(routines)

    # Add to scheduler if running
    if execute_fn:
        scheduler = _get_scheduler()
        if scheduler:
            try:
                _add_job(scheduler, routine, execute_fn)
            except Exception as e:
                log.warning("Failed to schedule routine '%s': %s", name, e)

    return f"Routine '{name}' created ({routine_type})."


def remove_routine(routine_id: str) -> str:
    """Remove a routine by ID."""
    with _lock:
        routines = _load_routines()
        found = False
        for r in routines:
            if r.get("id") == routine_id or r.get("name") == routine_id:
                routines.remove(r)
                found = True
                break
        if not found:
            return f"Routine '{routine_id}' not found."
        _save_routines(routines)

    # Remove from scheduler
    scheduler = _get_scheduler()
    if scheduler:
        try:
            scheduler.remove_job(routine_id)
        except Exception:
            pass

    return f"Routine '{routine_id}' removed."


def toggle_routine(routine_id: str, enabled: bool) -> str:
    """Enable or disable a routine."""
    with _lock:
        routines = _load_routines()
        for r in routines:
            if r.get("id") == routine_id or r.get("name") == routine_id:
                r["enabled"] = enabled
                _save_routines(routines)
                state = "enabled" if enabled else "disabled"
                return f"Routine '{r['name']}' {state}."
    return f"Routine '{routine_id}' not found."


def list_routines() -> list[dict]:
    """List all routines."""
    return _load_routines()


def get_routine(routine_id: str) -> dict | None:
    """Get a routine by ID."""
    for r in _load_routines():
        if r.get("id") == routine_id or r.get("name") == routine_id:
            return r
    return None
