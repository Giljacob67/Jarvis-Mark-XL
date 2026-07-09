"""JARVIS — snapshot do HUD da Fase 1.

Fonte única para o cliente de voz consultar estado atual, tarefa em execução
e logs recentes sem depender exclusivamente do data channel WebRTC.
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path

from core.permissions import AUDIT_PATH
from core.presence import get_presence


def _tail_jsonl(path: Path, limit: int) -> list[dict]:
    if limit <= 0 or not path.exists():
        return []
    lines: deque[str] = deque(maxlen=limit)
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(line)
    except Exception:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _presence_logs(limit: int) -> list[dict]:
    logs = []
    for ts, state, detail in get_presence().recent_history(limit):
        logs.append({
            "ts": ts,
            "type": "presence",
            "message": f"{state}{f' — {detail}' if detail else ''}",
        })
    return logs


def _audit_logs(limit: int) -> list[dict]:
    logs = []
    for entry in _tail_jsonl(AUDIT_PATH, limit):
        tool = entry.get("tool", "?")
        decision = entry.get("decision", "?")
        risk = entry.get("risk", "?")
        logs.append({
            "ts": entry.get("ts", ""),
            "type": "tool",
            "message": f"{tool}: {decision} ({risk})",
        })
    return logs


def hud_snapshot(log_limit: int = 12) -> dict:
    """Estado atual do assistente para HUDs locais/remotos."""
    presence = get_presence().snapshot()
    state = presence.get("state", "idle")
    detail = presence.get("detail", "")
    current_task = detail if state in {"executing_tool", "observing_screen", "thinking"} else ""
    logs = [*_presence_logs(log_limit), *_audit_logs(max(3, log_limit // 2))]
    return {
        "presence": presence,
        "current_task": current_task,
        "logs": logs[-log_limit:],
    }
