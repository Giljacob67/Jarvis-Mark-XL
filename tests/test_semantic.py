"""Tests for semantic memory retrieval (lexical path, no heavy deps)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.semantic import retrieve_relevant


def test_retrieve_empty_inputs():
    assert retrieve_relevant("", ["a", "b"]) == ""
    assert retrieve_relevant("x", []) == ""


def test_retrieve_ranks_relevant_first():
    cands = [
        "Receita federal prazo IRPF entrega",
        "Promoção de sapatos imperdível",
        "Audiência no tribunal data limite processo",
    ]
    out = retrieve_relevant("prazo jurídico tribunal", cands, k=1)
    assert "tribunal" in out.lower() or "audiência" in out.lower()


def test_retrieve_respects_k_and_bounds():
    cands = [f"item número {i} sobre assunto variado {i}" for i in range(20)]
    out = retrieve_relevant("item assunto", cands, k=3, max_chars=80)
    assert out.count("item") <= 3
    assert len(out) <= 80
