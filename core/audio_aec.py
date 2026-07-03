"""
MARK XL — Cancelamento de eco acústico (AEC) via PipeWire/WebRTC.

Carrega o module-echo-cancel do PipeWire (idempotente) e roteia o áudio
DESTE processo pelos dispositivos virtuais via PULSE_SOURCE/PULSE_SINK:

    jarvis_ec_src   → microfone com o eco do TTS matematicamente removido
    jarvis_ec_sink  → saída do Jarvis (vira o sinal de referência do AEC)

Com isso o microfone pode ficar ABERTO enquanto o Jarvis fala — barge-in
com caixas de som, sem fone — e o cooldown pós-fala cai de 2-4s para
~0.6s. É o coração da conversa fluida.

Falhou qualquer coisa (sem PipeWire, sem pactl)? Retorna False e o app
segue no modo antigo (gate de mic + cooldown longo). Nunca quebra o boot.
"""
from __future__ import annotations

import os
import subprocess

from core.logger import get_logger

log = get_logger("aec")

_SRC  = "jarvis_ec_src"
_SINK = "jarvis_ec_sink"


def _pactl(*args: str, timeout: int = 5) -> tuple[int, str]:
    try:
        r = subprocess.run(["pactl", *args], capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode, (r.stdout or r.stderr).strip()
    except Exception as e:
        return -1, str(e)


def _module_loaded() -> bool:
    code, out = _pactl("list", "modules", "short")
    return code == 0 and "module-echo-cancel" in out and _source_exists()


def _source_exists() -> bool:
    code, out = _pactl("list", "sources", "short")
    return code == 0 and _SRC in out


def ensure_echo_cancel(config: dict | None = None) -> bool:
    """Garante o AEC ativo e roteia o processo por ele. True = fluidez ON."""
    cfg = config or {}
    if not cfg.get("aec_enabled", True):
        return False

    if not _module_loaded():
        code, out = _pactl(
            "load-module", "module-echo-cancel",
            "aec_method=webrtc",
            f"source_name={_SRC}",
            f"sink_name={_SINK}",
        )
        if code != 0 or not _source_exists():
            log.warning("AEC indisponível (%s) — seguindo sem cancelamento de eco", out[:120])
            return False
        log.info("AEC WebRTC carregado (PipeWire)")

    # Env por-processo: libpulse honra na conexão — captura sai do source
    # cancelado e o TTS toca no sink de referência. Precisa acontecer ANTES
    # de qualquer stream abrir (chamado no início de main()).
    os.environ["PULSE_SOURCE"] = _SRC
    os.environ["PULSE_SINK"]   = _SINK
    log.info("Áudio roteado pelo AEC (%s / %s)", _SRC, _SINK)
    return True
