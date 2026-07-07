"""
JARVIS — Presence Engine: o estado vivo do assistente.

Máquina de estados leve e thread-safe que torna explícito o que o Jarvis
está fazendo AGORA (ouvindo, pensando, falando, executando, observando...),
com log claro de cada transição e listeners para UI/HUD e clientes remotos.

Deliberadamente SEM dependências de framework (Pipecat/Qt): o mapeamento
de eventos → estados é feito por quem integra (poc/bot.py na v2), o que
mantém este módulo 100% testável puro e reutilizável pelo satélite de
desktop e por sensores futuros (câmera, sistema, agenda).
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from enum import Enum

from core.logger import get_logger

log = get_logger("presence")


class PresenceState(str, Enum):
    IDLE              = "idle"
    LISTENING         = "listening"
    THINKING          = "thinking"
    SPEAKING          = "speaking"
    EXECUTING_TOOL    = "executing_tool"
    OBSERVING_SCREEN  = "observing_screen"    # reservado (Fase 2: satélite)
    PROACTIVE_WAITING = "proactive_waiting"   # reservado (migração proatividade)
    ERROR_RECOVERY    = "error_recovery"


# Transições que representam fluxos esperados. Qualquer outra é aceita mas
# logada como INESPERADA — sinal de bug de integração, não erro fatal
# (presença nunca pode derrubar o pipeline de voz).
_EXPECTED: dict[PresenceState, set[PresenceState]] = {
    PresenceState.IDLE:              {PresenceState.LISTENING, PresenceState.PROACTIVE_WAITING,
                                      PresenceState.SPEAKING},
    PresenceState.LISTENING:         {PresenceState.THINKING, PresenceState.IDLE,
                                      PresenceState.SPEAKING, PresenceState.OBSERVING_SCREEN},
    PresenceState.THINKING:          {PresenceState.SPEAKING, PresenceState.EXECUTING_TOOL,
                                      PresenceState.LISTENING, PresenceState.ERROR_RECOVERY},
    PresenceState.SPEAKING:          {PresenceState.LISTENING, PresenceState.IDLE,
                                      PresenceState.THINKING, PresenceState.ERROR_RECOVERY},
    PresenceState.EXECUTING_TOOL:    {PresenceState.THINKING, PresenceState.SPEAKING,
                                      PresenceState.LISTENING, PresenceState.ERROR_RECOVERY},
    PresenceState.OBSERVING_SCREEN:  {PresenceState.THINKING, PresenceState.LISTENING},
    PresenceState.PROACTIVE_WAITING: {PresenceState.SPEAKING, PresenceState.LISTENING,
                                      PresenceState.IDLE},
    PresenceState.ERROR_RECOVERY:    {PresenceState.LISTENING, PresenceState.IDLE},
}


class PresenceEngine:
    """Fonte única da verdade sobre o estado do assistente."""

    def __init__(self) -> None:
        self._state    = PresenceState.IDLE
        self._detail   = ""
        self._since    = time.time()
        self._lock     = threading.Lock()
        self._listeners: list[Callable[[PresenceState, str], None]] = []
        self._history: list[tuple[float, str, str]] = []   # (ts, estado, detalhe)

    # ── leitura ──────────────────────────────────────────────────────────
    @property
    def state(self) -> PresenceState:
        return self._state

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "state":   self._state.value,
                "detail":  self._detail,
                "since":   self._since,
                "elapsed": round(time.time() - self._since, 1),
            }

    def recent_history(self, n: int = 10) -> list[tuple[float, str, str]]:
        with self._lock:
            return list(self._history[-n:])

    # ── escrita ──────────────────────────────────────────────────────────
    def on_change(self, cb: Callable[[PresenceState, str], None]) -> None:
        """Registra listener (UI/HUD/clientes). Erros de listener são engolidos
        com log — presença jamais derruba o pipeline."""
        self._listeners.append(cb)

    def transition(self, new: PresenceState, detail: str = "") -> bool:
        """Muda de estado. Retorna False em no-op (mesmo estado+detalhe)."""
        with self._lock:
            if new == self._state and detail == self._detail:
                return False
            old = self._state
            expected = new in _EXPECTED.get(old, set())
            self._state  = new
            self._detail = detail
            self._since  = time.time()
            self._history.append((self._since, new.value, detail))
            if len(self._history) > 200:
                self._history = self._history[-100:]
        marker = "" if expected else " [INESPERADA]"
        log.info("presence: %s → %s%s%s", old.value, new.value,
                 f" ({detail})" if detail else "", marker)
        for cb in list(self._listeners):
            try:
                cb(new, detail)
            except Exception as e:
                log.warning("listener de presença falhou: %s", e)
        return True

    # açúcar semântico para os integradores
    def idle(self):                        self.transition(PresenceState.IDLE)
    def listening(self):                   self.transition(PresenceState.LISTENING)
    def thinking(self, detail: str = ""):  self.transition(PresenceState.THINKING, detail)
    def speaking(self, detail: str = ""):  self.transition(PresenceState.SPEAKING, detail)
    def executing(self, tool: str):        self.transition(PresenceState.EXECUTING_TOOL, tool)
    def error(self, detail: str = ""):     self.transition(PresenceState.ERROR_RECOVERY, detail)


# Singleton do processo — servidor v2, satélite e sensores compartilham.
_engine: PresenceEngine | None = None
_engine_lock = threading.Lock()


def get_presence() -> PresenceEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = PresenceEngine()
        return _engine
