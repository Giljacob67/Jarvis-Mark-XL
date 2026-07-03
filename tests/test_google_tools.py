"""Tests for the Google integration's pure logic (no network/credentials)."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.gcal_tool import parse_gcal_event
from actions import calendar_tool as ct


def test_parse_timed_event():
    ev = {"summary": "Audiência", "start": {"dateTime": "2026-07-10T14:30:00-03:00"}}
    out = parse_gcal_event(ev)
    assert out == {"title": "Audiência", "start": datetime(2026, 7, 10, 14, 30),
                   "all_day": False}


def test_parse_all_day_event():
    ev = {"summary": "Aniversário Mylena", "start": {"date": "2026-05-12"}}
    out = parse_gcal_event(ev)
    assert out["all_day"] is True
    assert out["start"] == datetime(2026, 5, 12)


def test_parse_event_without_start_is_none():
    assert parse_gcal_event({"summary": "?"}) is None
    assert parse_gcal_event({"summary": "?", "start": {"dateTime": "invalido"}}) is None


def test_agenda_upcoming_survives_google_failure(tmp_path, monkeypatch):
    """agenda_upcoming must keep working when the Google provider raises."""
    monkeypatch.setattr(ct, "_AGENDA_PATH", tmp_path / "agenda.json")
    from datetime import timedelta
    start = (datetime.now() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M")
    ct._agenda_create("Local", start)

    import core.google_auth as ga
    monkeypatch.setattr(ga, "google_ready", lambda: True)
    import actions.gcal_tool as gt
    def _boom(hours=48):
        raise RuntimeError("sem rede")
    monkeypatch.setattr(gt, "gcal_upcoming", _boom)

    out = ct.agenda_upcoming(hours=24)
    assert [e["title"] for e in out] == ["Local"]


def test_agenda_upcoming_merges_google_events(tmp_path, monkeypatch):
    monkeypatch.setattr(ct, "_AGENDA_PATH", tmp_path / "agenda.json")
    from datetime import timedelta
    now = datetime.now()
    ct._agenda_create("Local", (now + timedelta(hours=5)).strftime("%Y-%m-%d %H:%M"))

    import core.google_auth as ga
    monkeypatch.setattr(ga, "google_ready", lambda: True)
    import actions.gcal_tool as gt
    monkeypatch.setattr(gt, "gcal_upcoming", lambda hours=48: [
        {"title": "GCal", "start": now + timedelta(hours=2), "all_day": False},
    ])

    out = ct.agenda_upcoming(hours=24)
    assert [e["title"] for e in out] == ["GCal", "Local"]   # ordenado por início
