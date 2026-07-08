"""Observabilidade — heartbeats, staleness e diagnóstico falável."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import health


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "HEALTH_PATH", tmp_path / "health.json")


def test_beat_e_report(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    health.beat("proactive")
    r = health.report()
    assert r["services"]["proactive"]["alive"]
    assert "atrás" in r["services"]["proactive"]["last_beat"]
    assert r["services"]["radar"]["last_beat"] == "nunca"
    assert not r["services"]["radar"]["alive"]


def test_fail_registra_erro(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    health.beat("telegram")
    health.fail("telegram", "boom " * 100)
    r = health.report()["services"]["telegram"]
    assert "boom" in r["last_error"] and len(r["last_error"]) < 230


def test_stale_alerts_so_para_quem_ja_viveu(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert health.stale_alerts() == []          # nunca bateu: sem alarme
    health.beat("radar")
    assert health.stale_alerts() == []          # recém-batido: vivo
    # envelhece o heartbeat além de 3x o esperado
    data = health._load()
    data["radar"]["last_beat"] = time.time() - health._EXPECTED["radar"] * 4
    health._save(data)
    alerts = health.stale_alerts()
    assert len(alerts) == 1 and "radar" in alerts[0]


def test_speakable_maquina_sem_servicos(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    s = health.speakable()
    assert "VPS" in s                            # desktop: não alarma


def test_speakable_com_parado(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    health.beat("proactive")
    data = health._load()
    data["proactive"]["last_beat"] = time.time() - 999999
    health._save(data)
    s = health.speakable()
    assert "ATENÇÃO" in s and "proactive" in s


def test_age_str():
    assert health._age_str(30) == "30s atrás"
    assert health._age_str(600) == "10min atrás"
    assert "h atrás" in health._age_str(7200)
    assert "dias" in health._age_str(200000)
