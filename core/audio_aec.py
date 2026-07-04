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

import json
import os
import subprocess
import threading
import time

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

    # Env por-processo cobre o caminho libpulse. MAS o PortAudio costuma
    # sair pelo plugin ALSA→PipeWire, que IGNORA essas vars — por isso o
    # roteador abaixo move os streams explicitamente via pactl (única forma
    # confiável nos dois caminhos).
    os.environ["PULSE_SOURCE"] = _SRC
    os.environ["PULSE_SINK"]   = _SINK
    _start_stream_router()
    log.info("AEC ativo — roteador de streams ligado (%s / %s)", _SRC, _SINK)
    return True


# ---------------------------------------------------------------------------
# Roteador: garante que os streams DESTE processo usem os nós do AEC
# ---------------------------------------------------------------------------

_moved_nodes: set[int] = set()


def _pw_dump() -> list:
    try:
        r = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5)
        return json.loads(r.stdout) if r.returncode == 0 else []
    except Exception:
        return []


def _move_process_streams() -> None:
    """Move captura → jarvis_ec_src e playback → jarvis_ec_sink.

    O caminho ALSA→PipeWire do PortAudio não expõe o PID no pactl (streams
    aparecem sem application.process.id) e os índices do pactl não batem com
    os node ids do PipeWire — a única via confiável é pw-dump (identificação
    por pipewire.sec.pid do client) + pw-metadata target.object (verificado:
    os Links passam a apontar para os nós do AEC).
    """
    data = _pw_dump()
    if not data:
        return
    pid = os.getpid()
    clients = {
        o["id"]: ((o.get("info") or {}).get("props") or {}).get("pipewire.sec.pid")
        for o in data if o.get("type") == "PipeWire:Interface:Client"
    }
    targets: dict[str, int] = {}
    mine: list[tuple[int, str]] = []
    for o in data:
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        p = (o.get("info") or {}).get("props") or {}
        name = p.get("node.name")
        if name in (_SRC, _SINK):
            targets[name] = p.get("object.serial")
        mc = p.get("media.class") or ""
        if mc.startswith("Stream/") and clients.get(p.get("client.id")) == pid:
            mine.append((o["id"], mc))

    if len(targets) < 2:
        return
    for nid, mc in mine:
        if nid in _moved_nodes:
            continue
        tgt = _SINK if mc == "Stream/Output/Audio" else _SRC
        try:
            r = subprocess.run(
                ["pw-metadata", str(nid), "target.object", str(targets[tgt])],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                _moved_nodes.add(nid)
                log.info("stream #%d roteado → %s", nid, tgt)
        except Exception:
            pass


_router_started = False


def _start_stream_router() -> None:
    """Thread que re-verifica a cada 3s — cobre streams que (re)abrem depois
    (o InputStream do STT, o OutputStream persistente do TTS após reopen)."""
    global _router_started
    if _router_started:
        return
    _router_started = True

    def _loop() -> None:
        while True:
            try:
                _move_process_streams()
            except Exception:
                pass
            time.sleep(3)

    threading.Thread(target=_loop, daemon=True, name="aec-router").start()
