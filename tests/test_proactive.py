"""Tests for the proactive engine's pure logic and the local JSON agenda."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.proactive import in_quiet_hours, time_slot_due
from actions import calendar_tool as ct


def _dt(h, m):
    return datetime(2026, 7, 3, h, m)


# ── quiet hours ──────────────────────────────────────────────────────────

def test_quiet_hours_crossing_midnight():
    q = "22:30-07:00"
    assert in_quiet_hours(_dt(23, 0), q)
    assert in_quiet_hours(_dt(3, 0), q)
    assert not in_quiet_hours(_dt(12, 0), q)
    assert not in_quiet_hours(_dt(7, 0), q)     # end is exclusive
    assert in_quiet_hours(_dt(22, 30), q)       # start is inclusive


def test_quiet_hours_same_day_range():
    q = "13:00-14:00"
    assert in_quiet_hours(_dt(13, 30), q)
    assert not in_quiet_hours(_dt(14, 0), q)


def test_quiet_hours_invalid_string_is_permissive():
    assert not in_quiet_hours(_dt(23, 0), "invalido")


# ── time slots ───────────────────────────────────────────────────────────

def test_slot_due_within_grace():
    now = _dt(8, 45)
    assert time_slot_due(now, "08:30", last_done_date=None)


def test_slot_not_due_before_time():
    assert not time_slot_due(_dt(8, 0), "08:30", last_done_date=None)


def test_slot_not_due_after_grace():
    assert not time_slot_due(_dt(21, 0), "08:30", last_done_date=None)


def test_slot_not_repeated_same_day():
    now = _dt(8, 45)
    assert not time_slot_due(now, "08:30", last_done_date="2026-07-03")
    assert time_slot_due(now, "08:30", last_done_date="2026-07-02")


# ── pt-BR datetime parsing ───────────────────────────────────────────────

def test_parse_iso_and_br_formats():
    assert ct.parse_datetime_br("2026-07-10 09:00") == datetime(2026, 7, 10, 9, 0)
    assert ct.parse_datetime_br("10/07/2026 14:30") == datetime(2026, 7, 10, 14, 30)


def test_parse_relative_words():
    hoje = ct.parse_datetime_br("hoje 15:00")
    amanha = ct.parse_datetime_br("amanhã 09:30")
    now = datetime.now()
    assert hoje is not None and hoje.date() == now.date() and hoje.hour == 15
    assert amanha is not None and (amanha.date() - now.date()).days == 1


def test_parse_garbage_returns_none():
    assert ct.parse_datetime_br("depois de amanhã talvez") is None
    assert ct.parse_datetime_br("") is None


# ── local agenda create/list (isolated file) ─────────────────────────────

def test_agenda_create_and_upcoming(tmp_path, monkeypatch):
    monkeypatch.setattr(ct, "_AGENDA_PATH", tmp_path / "agenda.json")
    from datetime import timedelta
    start = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
    msg = ct._agenda_create("Reunião JGG", start)
    assert "Agendado" in msg
    upcoming = ct.agenda_upcoming(hours=24)
    assert len(upcoming) == 1
    assert upcoming[0]["title"] == "Reunião JGG"
    listed = ct._agenda_list(days=1)
    assert "Reunião JGG" in listed


def test_agenda_past_events_excluded(tmp_path, monkeypatch):
    monkeypatch.setattr(ct, "_AGENDA_PATH", tmp_path / "agenda.json")
    ct._save_agenda([{"title": "Antigo", "start": "2020-01-01 10:00"}])
    assert ct.agenda_upcoming(hours=48) == []
    assert "Nenhum compromisso" in ct._agenda_list(days=7)
