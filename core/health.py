"""
JARVIS — observabilidade dos serviços 24/7 (Fase 2).

O VPS trabalha sozinho; falha silenciosa aqui significa prazo perdido lá.
Cada serviço registra um heartbeat barato (memory/health.json) e qualquer
canal — voz ("Jarvis, como você está?"), Telegram, briefing — consegue
responder com um diagnóstico honesto:

  beat(nome)        serviço vivo: carimba o relógio (e conta erros zerados)
  fail(nome, erro)  registra a última falha do serviço
  report()          dict com idade de cada heartbeat + radar + sistema
  speakable()       o report em uma resposta falável pt-BR
  stale_alerts()    lista de serviços parados além do tolerado — o briefing
                    abre com isso quando existir

Limiares: cada serviço declara seu intervalo esperado em _EXPECTED (s);
parado = 3x o esperado. Tudo local, sem dependências novas.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from core.logger import get_logger

log = get_logger("health")

BASE_DIR = Path(__file__).resolve().parent.parent
HEALTH_PATH = BASE_DIR / "memory" / "health.json"

# intervalo esperado entre heartbeats (s); parado = 3x isso
_EXPECTED = {
    "proactive": 120,        # tick a cada 30s (folga p/ quiet hours é à parte)
    "telegram": 120,         # long-poll de 50s
    "radar": 4 * 3600,       # varredura padrão 60min, mas o silêncio noturno
                             # (22:30-07:00) segura a varredura por ~8.5h —
                             # 3x4h=12h só alarma parada real
}

_lock = threading.Lock()


def _load() -> dict:
    try:
        return json.loads(HEALTH_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                           encoding="utf-8")


def beat(name: str) -> None:
    """Serviço vivo. Barato o bastante para todo tick (arquivo minúsculo)."""
    with _lock:
        data = _load()
        svc = data.setdefault(name, {})
        svc["last_beat"] = time.time()
        svc["date"] = time.strftime("%Y-%m-%d %H:%M")
        try:
            _save(data)
        except Exception as e:
            log.warning("heartbeat %s não gravado: %s", name, e)


def fail(name: str, error: str) -> None:
    with _lock:
        data = _load()
        svc = data.setdefault(name, {})
        svc["last_error"] = str(error)[:200]
        svc["last_error_date"] = time.strftime("%Y-%m-%d %H:%M")
        svc["errors"] = int(svc.get("errors", 0)) + 1
        try:
            _save(data)
        except Exception:
            pass


def _age_str(secs: float) -> str:
    if secs < 90:
        return f"{int(secs)}s atrás"
    if secs < 5400:
        return f"{int(secs / 60)}min atrás"
    if secs < 90000:
        return f"{secs / 3600:.1f}h atrás"
    return f"{secs / 86400:.1f} dias atrás"


def report() -> dict:
    """Snapshot estruturado: serviços, radar, memória e máquina."""
    now = time.time()
    out: dict = {"services": {}, "system": {}}
    data = _load()
    for name, expected in _EXPECTED.items():
        svc = data.get(name, {})
        last = svc.get("last_beat")
        entry = {"alive": bool(last and now - last <= expected * 3)}
        entry["last_beat"] = _age_str(now - last) if last else "nunca"
        if svc.get("last_error"):
            entry["last_error"] = (f"{svc['last_error_date']}: "
                                   f"{svc['last_error']}")
        out["services"][name] = entry

    try:
        from poc.radar import pending
        out["radar_prazos_abertos"] = len(pending(days_ahead=365))
    except Exception:
        pass
    try:
        from memory.layered import get_memory
        mem = get_memory()
        out["memoria_episodios"] = len(mem._load())
        out["memoria_semantica"] = mem._semantic() is not None
    except Exception:
        pass

    try:
        import os
        import shutil
        du = shutil.disk_usage("/")
        out["system"]["disco_livre_gb"] = round(du.free / 2**30, 1)
        out["system"]["load"] = round(os.getloadavg()[0], 2)
    except Exception:
        pass
    return out


def stale_alerts() -> list[str]:
    """Serviços parados além do tolerado — vazio quando está tudo bem.

    Só acusa quem já bateu heartbeat alguma vez: no desktop (serviços
    desligados por design, VPS é o dono) não há o que alarmar.
    """
    now = time.time()
    data = _load()
    alerts = []
    for name, expected in _EXPECTED.items():
        last = data.get(name, {}).get("last_beat")
        if last and now - last > expected * 3:
            alerts.append(f"serviço '{name}' sem sinal de vida desde "
                          f"{_age_str(now - last)}")
    return alerts


def speakable() -> str:
    """'Jarvis, como você está?' — diagnóstico em uma resposta."""
    r = report()
    parts = []
    seen = [n for n, s in r["services"].items()
            if s["last_beat"] != "nunca"]
    # mesma regra do stale_alerts: quem nunca bateu não é falha (primeiro
    # ciclo ainda por vir), quem bateu e sumiu é
    dead = [n for n in seen if not r["services"][n]["alive"]]
    if not seen:
        parts.append("serviços 24/7 não rodam nesta máquina (são do VPS)")
    elif dead:
        parts.append("ATENÇÃO — parados: " + ", ".join(
            f"{n} (último sinal {r['services'][n]['last_beat']})"
            for n in dead))
    else:
        alive_txt = ", ".join(f"{n} {r['services'][n]['last_beat']}"
                              for n in seen)
        waiting = [n for n in r["services"] if n not in seen]
        if waiting:
            alive_txt += ("; aguardando primeiro ciclo: " +
                          ", ".join(waiting))
        parts.append("serviços ativos (" + alive_txt + ")")
    errs = [f"{n}: {s['last_error']}" for n, s in r["services"].items()
            if s.get("last_error")]
    if errs:
        parts.append("último erro — " + "; ".join(errs))
    if "radar_prazos_abertos" in r:
        parts.append(f"{r['radar_prazos_abertos']} prazo(s) em aberto no radar")
    if "memoria_episodios" in r:
        sem = "com busca semântica" if r.get("memoria_semantica") else \
              "busca por palavras"
        parts.append(f"{r['memoria_episodios']} memórias ({sem})")
    sysinfo = r.get("system", {})
    if sysinfo:
        parts.append(f"disco livre {sysinfo.get('disco_livre_gb', '?')} GB, "
                     f"carga {sysinfo.get('load', '?')}")
    return "Status: " + "; ".join(parts) + "."


HEALTH_TOOL_SCHEMA = {
    "name": "status_sistema",
    "description": "Diagnóstico do próprio JARVIS ('como você está?', 'está "
                   "tudo funcionando?', 'status do sistema'): serviços 24/7, "
                   "radar, memória, disco.",
    "properties": {},
    "required": [],
}
