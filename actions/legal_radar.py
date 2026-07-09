"""
MARK XL — Legal deadline radar.

Scans unread e-mail for court/tribunal deadlines (Projudi, PJe, TJ, intimações,
audiências, prazos de contestação/recurso). Surfaces them as calendar events
plus a spoken briefing. This is the highest-value feature for the user (a
lawyer). It is fully opt-in and fail-closed: any error returns an empty list,
so the proactive engine never nags or crashes because IMAP/Gmail hiccuped.
"""
from __future__ import annotations

import json
import re

from core.logger import get_logger

log = get_logger("legal_radar")

# Cheap pre-filter so we only spend an LLM call on plausibly legal mail.
_LEGAL_RE = re.compile(
    r"prazo|intima|audi[êe]n|senten[çc]a|cit[aç][ãa]o|despacho|processo|"
    r"projudi|pje|tj[s]?[a-z]?|f[óo]rum|vara|recurso|contest|decis|"
    r"tribunal|ajuiz|mandado|per[íi]cia",
    re.I,
)


def _looks_legal(text: str) -> bool:
    return bool(_LEGAL_RE.search(text or ""))


def extract_deadlines(bodies: list[dict]) -> list[dict]:
    """Given [{'sender','subject','body'}], return legal deadlines as
    [{'title','deadline','source'}] using the LLM to parse dates.

    Network-free except for the single LLM call; safe to unit-test with mocks.
    """
    candidates = [
        b for b in bodies
        if _looks_legal(b.get("subject", "")) or _looks_legal(b.get("body", ""))
    ]
    if not candidates:
        return []

    # Batch up to 8 candidates into one LLM call to keep cost low.
    blob = "\n\n---\n\n".join(
        f"ASSUNTO: {c.get('subject', '')}\nDE: {c.get('sender', '')}\n"
        f"{(c.get('body', '') or '')[:2000]}"
        for c in candidates[:8]
    )
    prompt = (
        "Estes sao e-mails juridicos. Extraia TODOS os prazos processuais "
        "(datas-limite, audiencias, contestacoes, recursos, intimacoes) "
        "mencionados. Responda SOMENTE com JSON: "
        '[{"title": <resumo de 1-8 palavras>, "deadline": <"YYYY-MM-DD HH:MM" '
        'ou "YYYY-MM-DD">, "source": <remetente ou tribunal>}]'
        ". Se nao houver prazo concreto, retorne []. Nunca invente datas."
    )
    try:
        from core.llm_client import call_llm_text
        out = call_llm_text(blob, system=prompt, timeout=60)
    except Exception as e:
        log.warning("LLM parse falhou: %s", e)
        return []

    try:
        data = json.loads(re.sub(r"```(?:json)?", "", out).strip().rstrip("`").strip())
    except Exception:
        return []
    if not isinstance(data, list):
        return []

    from actions.calendar_tool import parse_datetime_br

    results: list[dict] = []
    for d in data:
        if not isinstance(d, dict):
            continue
        dl = d.get("deadline")
        if not dl:
            continue
        dt = parse_datetime_br(str(dl))
        if dt is None:
            continue
        results.append({
            "title": str(d.get("title") or d.get("source") or "Prazo juridico")[:80],
            "deadline": dt.strftime("%Y-%m-%d %H:%M"),
            "source": str(d.get("source") or ""),
        })
    return results


def scan_legal_deadlines(limit: int = 10) -> list[dict]:
    """Fetch recent unread bodies and extract legal deadlines."""
    try:
        from actions.email_tool import fetch_recent_bodies
        bodies = fetch_recent_bodies(limit)
    except Exception as e:
        log.warning("fetch falhou: %s", e)
        return []
    return extract_deadlines(bodies)
