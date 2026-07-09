"""Tests for continuous vision (screen cache + diff)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.screen_cache import save, get_last


def test_screen_cache_roundtrip(tmp_path):
    p = tmp_path / "screen_cache.json"
    with patch("memory.screen_cache._PATH", p):
        save("tela A")
        desc, ts = get_last()
        assert desc == "tela A"
        assert ts > 0


def test_describe_diff_no_change():
    import actions.screen_processor as sp
    with patch("core.llm_client.call_llm_text",
               return_value="Nenhuma mudanca detectada."):
        assert "nenhuma" in sp._describe_diff("igual", "igual").lower()


def test_screen_process_diff(monkeypatch):
    import actions.screen_processor as sp
    monkeypatch.setattr(sp, "_capture_screen", lambda: (b"x", "image/jpeg"))
    monkeypatch.setattr(sp, "_call_vision", lambda b, m, t: "Tela atual: grafico")

    with patch("memory.screen_cache.get_last", return_value=("Tela anterior: texto", 0.0)), \
         patch("memory.screen_cache.save") as msave, \
         patch("core.llm_client.call_llm_text",
               return_value="O grafico apareceu no lugar do texto"):
        out = sp.screen_process(
            parameters={"angle": "screen", "mode": "diff", "text": "o que mudou?"})

    assert "grafico" in out.lower()
    msave.assert_called_once()


def test_screen_process_describe_caches(monkeypatch):
    import actions.screen_processor as sp
    monkeypatch.setattr(sp, "_capture_screen", lambda: (b"x", "image/jpeg"))
    monkeypatch.setattr(sp, "_call_vision", lambda b, m, t: "Tela mostra X")

    with patch("memory.screen_cache.get_last", return_value=("", 0.0)), \
         patch("memory.screen_cache.save") as msave:
        out = sp.screen_process(parameters={"angle": "screen"})

    assert out == "Tela mostra X"
    msave.assert_called_once()
