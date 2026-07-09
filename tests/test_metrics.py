"""Tests for the observability metrics collector."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import metrics


def test_record_and_snapshot():
    metrics.record("stt", 120.0)
    metrics.record("stt", 200.0)
    snap = metrics.snapshot()
    assert "stt" in snap
    assert snap["stt"]["n"] == 2
    assert snap["stt"]["avg"] == 160.0
    assert snap["stt"]["min"] == 120.0
    assert snap["stt"]["max"] == 200.0


def test_record_ignores_invalid():
    before = metrics.snapshot()
    metrics.record("x", -5)
    metrics.record("x", None)
    assert "x" not in metrics.snapshot()


def test_summary_line_returns_string():
    metrics.record("llm_ttft", 800.0)
    assert isinstance(metrics.summary_line(), str)
