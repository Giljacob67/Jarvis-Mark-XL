"""Registry unificado — schemas resolvem, ambiente filtra, dispatch acha.

Importa poc/* (loguru) — na venv Pipecat roda; no Python do sistema pula.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("loguru")

from core.registry import all_tools, dispatch, tools_for

_HEADLESS = {"ui": None, "say": lambda t: None,
             "satellite": None, "has_display": False}
_DESKTOP = {**_HEADLESS, "has_display": True}
_VPS_SAT = {**_HEADLESS, "satellite": lambda *a, **k: "rpc"}


def test_todos_os_schemas_resolvem():
    for t in all_tools().values():
        s = t.schema()
        assert s["name"] == t.name and s["description"]
        assert isinstance(s["properties"], dict)
        assert isinstance(s["required"], list)


def test_ambiente_filtra_ferramentas_de_desktop():
    names = lambda ctx: {t.name for t in tools_for(ctx)}
    headless = names(_HEADLESS)
    assert "open_app" not in headless and "screen_look" not in headless
    assert {"calendar", "email_tool", "radar_prazos",
            "status_sistema"} <= headless
    assert {"open_app", "screen_look"} <= names(_DESKTOP)
    assert {"open_app", "screen_look"} <= names(_VPS_SAT)


def test_dispatch_desconhecida_e_rpc():
    assert "desconhecida" in dispatch("nao_existe", {}, _HEADLESS)
    # open_app sem display + com satélite → vai por RPC
    assert dispatch("open_app", {"app_name": "calc"}, _VPS_SAT) == "rpc"
    assert "satélite não configurado" in dispatch("screen_look", {}, _HEADLESS)


def test_dispatch_status_sistema():
    assert dispatch("status_sistema", {}, _HEADLESS).startswith("Status:")


def test_registry_cobre_o_que_o_bridge_expunha():
    esperadas = {"calendar", "email_tool", "web_search", "notes", "timer",
                 "open_app", "screen_look", "briefing", "radar_prazos",
                 "status_sistema", "personalidade", "remember", "recall",
                 "forget", "context_summary"}
    assert esperadas == set(all_tools().keys())
