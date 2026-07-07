"""Fase 1 — testes: Presence Engine, Permissions Layer e Memória em camadas."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.presence import PresenceEngine, PresenceState
from core.permissions import decide, risk_of, is_confirmed, confirmation_request
from memory.layered import LayeredMemory


# ── Presence ─────────────────────────────────────────────────────────────

def test_presence_transitions_and_history():
    p = PresenceEngine()
    assert p.state == PresenceState.IDLE
    assert p.transition(PresenceState.LISTENING)
    p.thinking("processando pergunta")
    p.executing("email_tool")
    p.speaking()
    assert p.state == PresenceState.SPEAKING
    hist = p.recent_history()
    assert [h[1] for h in hist] == ["listening", "thinking", "executing_tool", "speaking"]


def test_presence_noop_suppressed():
    p = PresenceEngine()
    p.listening()
    assert not p.transition(PresenceState.LISTENING)   # mesmo estado+detalhe


def test_presence_listeners_and_resilience():
    p = PresenceEngine()
    seen = []
    p.on_change(lambda s, d: seen.append((s.value, d)))
    p.on_change(lambda s, d: 1 / 0)          # listener quebrado não derruba
    p.executing("calendar")
    assert seen == [("executing_tool", "calendar")]
    assert p.snapshot()["state"] == "executing_tool"


def test_presence_unexpected_transition_allowed():
    p = PresenceEngine()
    # IDLE → EXECUTING_TOOL não é fluxo esperado, mas nunca deve falhar
    assert p.transition(PresenceState.EXECUTING_TOOL, "boot-tool")


# ── Permissions ──────────────────────────────────────────────────────────

def test_risk_matrix_action_aware():
    assert risk_of("email_tool", {"action": "read"}) == "low"
    assert risk_of("email_tool", {"action": "send"}) == "high"
    assert risk_of("calendar", {"action": "list"}) == "low"
    assert risk_of("calendar", {"action": "create"}) == "medium"
    assert risk_of("ferramenta_desconhecida", {}) == "medium"   # nunca 'low'


def test_supervised_requires_confirmation_for_high():
    d, _ = decide("email_tool", {"action": "send", "to": "x@y.com"}, "supervised")
    assert d == "confirm"
    d, _ = decide("email_tool", {"action": "send", "confirm": "sim"}, "supervised")
    assert d == "allow"
    d, _ = decide("email_tool", {"action": "read"}, "supervised")
    assert d == "allow"


def test_read_only_denies_writes():
    assert decide("email_tool", {"action": "read"}, "read_only")[0] == "allow"
    assert decide("notes", {"action": "add"}, "read_only")[0] == "deny"
    assert decide("email_tool", {"action": "send", "confirm": "sim"},
                  "read_only")[0] == "deny"   # confirmação não fura o read-only


def test_autonomous_allows_all():
    assert decide("computer_control", {}, "autonomous")[0] == "allow"


def test_confirmation_parsing_and_request_text():
    assert is_confirmed({"confirm": "SIM"})
    assert not is_confirmed({"confirm": "não"})
    msg = confirmation_request("email_tool", {"action": "send", "to": "a@b.c"})
    assert "confirm='sim'" in msg and "risco alto" in msg


# ── Memória em camadas ───────────────────────────────────────────────────

def _mem(tmp_path):
    return LayeredMemory(episodic_path=tmp_path / "epi.jsonl")


def test_memory_remember_and_recall(tmp_path):
    m = _mem(tmp_path)
    m.remember("O cliente Fazenda Santa Rita prefere reuniões às terças")
    m.remember("Senha do wifi do escritório trocada em julho")
    out = m.recall("fazenda santa rita")
    assert "Santa Rita" in out
    out2 = m.recall("reunioes")           # busca sem acento acha 'reuniões'
    assert "Santa Rita" in out2


def test_memory_forget_tombstones(tmp_path):
    m = _mem(tmp_path)
    m.remember("Processo confidencial da empresa X")
    m.remember("Almoço com Felipe na sexta")
    assert "confidencial" in m.recall("processo confidencial")
    m.forget("processo confidencial")
    assert "Não tenho nada" in m.recall("processo confidencial")
    assert "Felipe" in m.recall("almoço felipe")      # o resto sobrevive
    assert m.purge() == 1                              # compactação física


def test_memory_operational_and_context(tmp_path):
    m = _mem(tmp_path)
    m.op_set("estado", "ouvindo")
    m.op_action("consultou agenda")
    m.op_action("leu 5 e-mails")
    m.remember("nota recente")
    ctx = m.context_summary()
    assert "ouvindo" in ctx and "e-mails" in ctx and "nota recente" in ctx


def test_memory_empty_context(tmp_path):
    assert "Sem contexto" in _mem(tmp_path).context_summary()
