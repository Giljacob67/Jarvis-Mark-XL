"""
JARVIS v2 — Daily Briefing Engine.

Coleta determinística (agenda Google, e-mails não lidos, clima Maringá,
memória episódica recente, pendências) → fraseio natural pelo LLM em
pt-BR, nos modos curto / medio / completo. Histórico em
memory/briefings.jsonl. Usado:
  - sob demanda (ferramenta `briefing`: "me dê meu briefing",
    "no que devo focar hoje?")
  - pela proatividade matinal (poc/services.py), falado e/ou via Telegram.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from loguru import logger

HISTORY_PATH = BASE_DIR / "memory" / "briefings.jsonl"

# Maringá/PR — clima via Open-Meteo (sem chave, resposta em ~200ms)
_LAT, _LON = -23.42, -51.93
_WEATHER_CODES = {
    0: "céu limpo", 1: "quase limpo", 2: "parcialmente nublado", 3: "nublado",
    45: "neblina", 48: "neblina", 51: "garoa", 53: "garoa", 55: "garoa forte",
    61: "chuva fraca", 63: "chuva", 65: "chuva forte", 80: "pancadas",
    81: "pancadas", 82: "pancadas fortes", 95: "tempestade",
}


def _weather() -> str:
    try:
        import requests
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": _LAT, "longitude": _LON,
                    "current": "temperature_2m,weather_code",
                    "daily": "temperature_2m_max,temperature_2m_min,"
                             "precipitation_probability_max",
                    "timezone": "America/Sao_Paulo", "forecast_days": 1},
            timeout=6,
        ).json()
        cur = r.get("current", {})
        day = r.get("daily", {})
        desc = _WEATHER_CODES.get(cur.get("weather_code", -1), "")
        return (f"{round(cur.get('temperature_2m', 0))}°C agora ({desc}), "
                f"máx {round(day['temperature_2m_max'][0])}°, "
                f"mín {round(day['temperature_2m_min'][0])}°, "
                f"chuva {day['precipitation_probability_max'][0]}%")
    except Exception as e:
        logger.debug(f"clima indisponível: {e}")
        return ""


def gather_facts() -> dict:
    """Coleta bruta de todas as fontes (cada uma falha isolada)."""
    facts: dict[str, str] = {}
    try:
        from core.health import stale_alerts
        alerts = stale_alerts()
        if alerts:   # o vigia parado é a notícia mais urgente do dia
            facts["ALERTA"] = ("; ".join(alerts) +
                               " — prazos podem estar passando sem detecção!")
    except Exception:
        pass
    try:
        from poc.radar import pending, speakable
        if pending(10):
            facts["prazos"] = speakable(10)   # vem primeiro: prioridade máxima
    except Exception as e:
        logger.debug(f"radar indisponível no briefing: {e}")
    try:
        from actions.calendar_tool import calendar_tool
        facts["agenda"] = calendar_tool(parameters={"action": "list", "days": 2})
    except Exception as e:
        facts["agenda"] = f"(agenda indisponível: {e})"
    try:
        from actions.email_tool import unread_summary
        n, top = unread_summary(limit=3)
        facts["emails"] = (f"{n} não lidos; recentes: " +
                           "; ".join(f"{s} — {a}" for s, a in top)) if n else \
            "caixa de entrada em dia"
    except Exception as e:
        facts["emails"] = f"(e-mail indisponível: {e})"
    w = _weather()
    if w:
        facts["clima"] = w
    try:
        from memory.layered import get_memory
        m = get_memory()
        recent = m.recall("prazo audiência reunião compromisso", limit=3)
        if "Não tenho" not in recent:
            facts["memoria"] = recent
        facts["contexto"] = m.context_summary()
    except Exception:
        pass
    return facts


_MODE_RULES = {
    "curto":    "máximo 2 frases: só o que exige ação hoje.",
    "medio":    "3-5 frases: agenda, e-mails que importam, clima em meia frase.",
    "completo": "6-9 frases: agenda, e-mails, clima, memória relevante e um "
                "resumo estratégico de foco do dia.",
}


def generate(mode: str = "medio", question: str | None = None) -> str:
    """Gera o briefing fraseado. `question` personaliza ('no que focar?')."""
    mode = mode if mode in _MODE_RULES else "medio"
    facts = gather_facts()
    facts_txt = "\n".join(f"- {k}: {v}" for k, v in facts.items())

    from poc.text_agent import _client_and_model
    client, model = _client_and_model()
    ask = question or "Briefing do dia."
    sys_p = (
        "Você é o JARVIS falando o briefing matinal do Dr. Gilberto (advogado, "
        "Maringá/PR) em português brasileiro. Fale NATURALMENTE, como um chefe "
        f"de gabinete — jamais como lista robótica. Formato: {_MODE_RULES[mode]} "
        "Números por extenso quando falados — MAS números de processo/CNPJ "
        "NUNCA soletre: refira-se de forma curta ('a execução fiscal de "
        "Balneário Arroio do Silva'). Não invente NADA além dos fatos. "
        "Se um prazo processual aparecer na agenda, ele vem PRIMEIRO."
    )
    try:
        resp = client.chat.completions.create(
            model=model, max_tokens=1500,
            messages=[{"role": "system", "content": sys_p},
                      {"role": "user", "content": f"{ask}\n\nFATOS:\n{facts_txt}"}],
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning(f"fraseio do briefing falhou ({e}) — template")
        text = "Bom dia, senhor. " + " ".join(f"{k}: {v}." for k, v in facts.items())

    _save_history(mode, text)
    return text


def _save_history(mode: str, text: str) -> None:
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M"),
                                "mode": mode, "text": text},
                               ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"histórico de briefing falhou: {e}")


BRIEFING_TOOL_SCHEMA = {
    "name": "briefing",
    "description": "Gera o briefing do dia ('me dê meu briefing', 'no que devo "
                   "focar hoje?', 'como está meu dia?'). Junta agenda, e-mails, "
                   "clima e memória.",
    "properties": {
        "mode": {"type": "string",
                 "description": "curto | medio | completo (padrão: medio)"},
        "question": {"type": "string",
                     "description": "Pergunta específica do usuário, se houver"},
    },
    "required": [],
}
