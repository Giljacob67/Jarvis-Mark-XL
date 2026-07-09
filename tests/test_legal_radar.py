"""Tests for the legal deadline radar (network-free paths)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.legal_radar import _looks_legal, extract_deadlines


def test_looks_legal_detects_prazo():
    assert _looks_legal("Prazo para contestação de mérito")
    assert _looks_legal("Intimação disponível no Projudi")
    assert not _looks_legal("Promoção imperdível de hoje")


def test_extract_empty_when_no_bodies():
    assert extract_deadlines([]) == []


def test_extract_skips_non_legal_mail():
    bodies = [{"sender": "loja@x.com", "subject": "Promoção", "body": "Compre já!"}]
    assert extract_deadlines(bodies) == []


def test_extract_parses_deadline_via_llm():
    bodies = [{
        "sender": "TJPR", "subject": "Intimação - prazo de contestação",
        "body": "Fica Vossa Senhoria intimado, prazo 15/08/2026.",
    }]
    fake = (
        '[{"title": "Contestação", "deadline": "2026-08-15 23:59", '
        '"source": "TJPR"}]'
    )
    with patch("core.llm_client.call_llm_text", return_value=fake):
        out = extract_deadlines(bodies)
    assert len(out) == 1
    assert out[0]["title"] == "Contestação"
    assert out[0]["deadline"] == "2026-08-15 23:59"
    assert out[0]["source"] == "TJPR"


def test_extract_drops_unparseable_date():
    bodies = [{
        "sender": "TJPR", "subject": "Prazo procesual",
        "body": "prazo importante",
    }]
    fake = '[{"title": "X", "deadline": "em breve", "source": "TJPR"}]'
    with patch("core.llm_client.call_llm_text", return_value=fake):
        assert extract_deadlines(bodies) == []
