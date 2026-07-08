"""
JARVIS v2 — Radar de Prazos Jurídico (Fase 4 do roadmap).

Missão: NENHUMA intimação passa despercebida. O radar:

  1. varre o Gmail por e-mails de tribunal (remetentes *.jus.br — Projudi,
     PJe, e-Proc etc.; query configurável em radar_query)
  2. extrai os dados do ato via LLM (processo, ato, data de ciência,
     prazo em dias, contagem) — SÓ extração; a matemática é nossa
  3. calcula a data-limite DETERMINISTICAMENTE: dias úteis (CPC art. 219),
     exclui o dia do começo e inclui o do vencimento (art. 224), feriados
     nacionais + forenses PR/Maringá, recesso forense 20/dez–20/jan
     (art. 220 suspende a contagem)
  4. registra em memory/prazos.jsonl, cria evento no Google Calendar e
     alimenta o briefing matinal

O radar é um VIGIA, não um contador oficial: toda data sai marcada como
estimativa para o Dr. Gilberto confirmar no sistema do tribunal — regras
locais (suspensões, feriados estaduais de outros TJs, prazo em dobro)
fogem do cálculo genérico.

Dedupe: memory/radar_state.json guarda os IDs Gmail já processados.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# core.logger (stdlib): mantém o módulo importável fora da venv Pipecat
# (loguru não existe no Python do sistema, onde a suíte de testes roda)
from core.logger import get_logger

logger = get_logger("radar")

STATE_PATH = BASE_DIR / "memory" / "radar_state.json"
PRAZOS_PATH = BASE_DIR / "memory" / "prazos.jsonl"

_DEFAULT_QUERY = ("from:(jus.br) newer_than:{days}d "
                  "-subject:(newsletter OR informativo)")


def _cfg() -> dict:
    try:
        return json.loads((BASE_DIR / "config" / "api_keys.json")
                          .read_text(encoding="utf-8"))
    except Exception:
        return {}


# ── calendário forense ────────────────────────────────────────────────────

def _easter(year: int) -> date:
    """Domingo de Páscoa (algoritmo de Gauss/Meeus)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month = (h + ll - 7 * m + 114) // 31
    day = ((h + ll - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def holidays(year: int) -> set[date]:
    """Feriados nacionais + dias sem expediente forense (PR/Maringá)."""
    e = _easter(year)
    hs = {
        date(year, 1, 1), date(year, 4, 21), date(year, 5, 1),
        date(year, 9, 7), date(year, 10, 12), date(year, 11, 2),
        date(year, 11, 15), date(year, 11, 20), date(year, 12, 25),
        e - timedelta(days=48), e - timedelta(days=47),   # carnaval seg/ter
        e - timedelta(days=2),                            # sexta-feira santa
        e + timedelta(days=60),                           # corpus christi
        date(year, 12, 19),                               # emancipação do PR
        date(year, 5, 10),                                # aniversário Maringá
        date(year, 12, 8),                                # padroeira Maringá
    }
    return hs


def _in_recess(d: date) -> bool:
    """Recesso forense 20/dez–20/jan: prazos suspensos (CPC art. 220)."""
    return ((d.month == 12 and d.day >= 20) or
            (d.month == 1 and d.day <= 20))


def is_business_day(d: date) -> bool:
    return (d.weekday() < 5 and d not in holidays(d.year)
            and not _in_recess(d))


def compute_deadline(ciencia: date, prazo_dias: int,
                     contagem: str = "uteis") -> date:
    """Data-limite: exclui o dia do começo, inclui o do vencimento."""
    if contagem == "corridos":
        d = ciencia + timedelta(days=prazo_dias)
        while not is_business_day(d):     # vencimento em dia sem expediente
            d += timedelta(days=1)        # prorroga (CPC art. 224 §1º)
        return d
    d, counted = ciencia, 0
    while counted < prazo_dias:
        d += timedelta(days=1)
        if is_business_day(d):
            counted += 1
    return d


# ── leitura do Gmail ──────────────────────────────────────────────────────

def _body_text(payload: dict) -> str:
    """Extrai texto do corpo (text/plain; fallback: HTML sem tags)."""
    import base64

    def _decode(part: dict) -> str:
        data = (part.get("body") or {}).get("data", "")
        if not data:
            return ""
        return base64.urlsafe_b64decode(data + "===").decode("utf-8", "replace")

    stack, plain, html = [payload], [], []
    while stack:
        p = stack.pop()
        stack.extend(p.get("parts") or [])
        mime = p.get("mimeType", "")
        if mime == "text/plain":
            plain.append(_decode(p))
        elif mime == "text/html":
            html.append(_decode(p))
    text = "\n".join(plain).strip()
    if not text and html:
        text = re.sub(r"<[^>]+>", " ", "\n".join(html))
        text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def _fetch_court_emails(days: int, limit: int = 20) -> list[dict]:
    """E-mails de tribunal ainda não processados: [{id, from, subject, date, body}]."""
    from actions.email_tool import _gmail_service
    svc = _gmail_service()
    query = _cfg().get("radar_query", _DEFAULT_QUERY).format(days=days)
    resp = svc.users().messages().list(
        userId="me", q=query, maxResults=limit).execute()
    seen = set(_load_state().get("processed_ids", []))
    out = []
    for m in resp.get("messages", []):
        if m["id"] in seen:
            continue
        full = svc.users().messages().get(
            userId="me", id=m["id"], format="full").execute()
        hdrs = {h["name"].lower(): h["value"]
                for h in full.get("payload", {}).get("headers", [])}
        out.append({
            "id": m["id"],
            "from": hdrs.get("from", ""),
            "subject": hdrs.get("subject", ""),
            "date": hdrs.get("date", ""),
            "body": _body_text(full.get("payload", {}))[:6000],
        })
    return out


# ── extração via LLM (só extração; matemática é determinística) ───────────

_EXTRACT_PROMPT = """Você extrai dados de intimações judiciais brasileiras \
(Projudi, PJe, e-Proc). Responda SOMENTE um JSON válido, sem markdown:
{"tem_prazo": true/false,
 "processo": "número CNJ ou vazio",
 "tribunal": "sigla (TJPR, TRF4...)",
 "ato": "tipo do ato em poucas palavras (contestação, embargos, ciência...)",
 "data_ciencia": "YYYY-MM-DD (data da intimação/publicação/leitura no e-mail)",
 "prazo_dias": número inteiro (0 se sem prazo),
 "contagem": "uteis" ou "corridos",
 "data_limite_explicita": "YYYY-MM-DD se o e-mail JÁ informa a data fatal, senão vazio",
 "presumido": true/false,
 "resumo": "1 frase: o que fazer e em qual processo"}
Regras: prazos processuais do CPC são em dias ÚTEIS salvo menção contrária. \
tem_prazo=false SÓ para movimentação claramente sem ônus (juntada de \
documento, mera tramitação). ATENÇÃO — "Confirmada a intimação eletrônica" \
ou "decurso de prazo de consulta" significa que a intimação SE APERFEIÇOOU \
e o prazo do ato COMEÇOU A CORRER, mesmo que o e-mail se diga "meramente \
informativo": nesse caso tem_prazo=true; se o e-mail não disser qual é o \
ato/prazo, use prazo_dias=15, contagem="uteis" e presumido=true (prazo \
padrão do CPC, a conferir no sistema). presumido=false quando o prazo está \
dito no e-mail. NUNCA invente datas: se a data de ciência não estiver \
clara, use a data do e-mail."""


def _parse_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _extract(email: dict) -> dict | None:
    from poc.text_agent import _client_and_model
    client, model = _client_and_model()
    content = (f"E-mail de {email['from']} em {email['date']}\n"
               f"Assunto: {email['subject']}\n\n{email['body']}")
    try:
        resp = client.chat.completions.create(
            model=model, max_tokens=1500,   # gpt-oss: reasoning consome orçamento
            messages=[{"role": "system", "content": _EXTRACT_PROMPT},
                      {"role": "user", "content": content}],
        )
        return _parse_json(resp.choices[0].message.content or "")
    except Exception as e:
        logger.error(f"radar: extração LLM falhou: {e}")
        return None


# ── persistência ──────────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # mantém só os 500 IDs mais recentes — Gmail não devolve mais que isso
    state["processed_ids"] = state.get("processed_ids", [])[-500:]
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False),
                          encoding="utf-8")


def load_prazos() -> list[dict]:
    out = []
    try:
        for line in PRAZOS_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    except FileNotFoundError:
        pass
    return out


def _append_prazo(entry: dict) -> None:
    PRAZOS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PRAZOS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── varredura (chamada pela proatividade e pela ferramenta) ───────────────

def scan(days: int = 7) -> list[dict]:
    """Varre, extrai, calcula, agenda. Retorna os prazos NOVOS encontrados."""
    try:
        emails = _fetch_court_emails(days)
    except Exception as e:
        logger.warning(f"radar: Gmail indisponível: {e}")
        return []
    state = _load_state()
    processed = state.setdefault("processed_ids", [])
    found = []
    for em in emails:
        processed.append(em["id"])
        info = _extract(em)
        if not info:
            continue    # extração falhou — NÃO marca como sem-prazo
        if not info.get("tem_prazo"):
            logger.info(f"radar: sem prazo — {em['subject'][:60]}")
            continue
        try:
            entry = _build_entry(em, info)
        except Exception as e:
            logger.error(f"radar: entrada inválida ({e}) — {info}")
            continue
        _append_prazo(entry)
        _create_event(entry)
        found.append(entry)
        logger.info(f"radar: PRAZO {entry['data_limite']} — {entry['resumo']}")
    _save_state(state)
    return found


def _build_entry(em: dict, info: dict) -> dict:
    explicit = (info.get("data_limite_explicita") or "").strip()
    if explicit:
        limite = date.fromisoformat(explicit)
    else:
        ciencia = date.fromisoformat(info["data_ciencia"])
        limite = compute_deadline(ciencia, int(info["prazo_dias"]),
                                  info.get("contagem", "uteis"))
    return {
        "gmail_id": em["id"],
        "processo": info.get("processo", ""),
        "tribunal": info.get("tribunal", ""),
        "ato": info.get("ato", ""),
        "data_ciencia": info.get("data_ciencia", ""),
        "prazo_dias": int(info.get("prazo_dias", 0)),
        "contagem": info.get("contagem", "uteis"),
        "data_limite": limite.isoformat(),
        "explicita": bool(explicit),
        "presumido": bool(info.get("presumido")),
        "resumo": info.get("resumo", ""),
        "status": "aberto",
        "ts": time.strftime("%Y-%m-%d %H:%M"),
    }


def _create_event(entry: dict) -> None:
    try:
        from actions.gcal_tool import gcal_create
        start = datetime.fromisoformat(entry["data_limite"] + "T09:00:00")
        proc = entry["processo"] or "processo não identificado"
        r = gcal_create(f"⚖️ PRAZO: {entry['ato']} — {proc} (conferir!)",
                        start, duration_min=60)
        logger.info(f"radar: evento criado — {r}")
    except Exception as e:
        logger.warning(f"radar: Calendar falhou ({e}) — prazo segue no jsonl")


# ── consulta (ferramenta de voz + briefing) ───────────────────────────────

def pending(days_ahead: int = 15) -> list[dict]:
    """Prazos abertos com vencimento até `days_ahead` dias (vencidos incluem)."""
    cutoff = (date.today() + timedelta(days=days_ahead)).isoformat()
    seen: dict[str, dict] = {}
    for p in load_prazos():
        seen[p["gmail_id"]] = p       # última linha vence (permite baixas)
    out = [p for p in seen.values()
           if p.get("status") == "aberto" and p["data_limite"] <= cutoff]
    return sorted(out, key=lambda p: p["data_limite"])


def speakable(days_ahead: int = 15) -> str:
    """Resumo falável dos prazos pendentes (usado no briefing e na tool)."""
    ps = pending(days_ahead)
    if not ps:
        return f"Nenhum prazo nos próximos {days_ahead} dias no radar."
    today = date.today().isoformat()
    lines = []
    for p in ps:
        d = date.fromisoformat(p["data_limite"])
        when = ("VENCIDO" if p["data_limite"] < today else
                "HOJE" if p["data_limite"] == today else
                f"{d.strftime('%d/%m')} ({(d - date.today()).days} dias)")
        lines.append(f"{when}: {p['resumo'] or p['ato']}"
                     + (f" [{p['processo']}]" if p["processo"] else "")
                     + (" — prazo PRESUMIDO, confira o ato"
                        if p.get("presumido") else ""))
    return (f"{len(ps)} prazo(s) no radar (estimativas — confirme no "
            "sistema): " + "; ".join(lines))


def _normalize(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def settle(query: str) -> str:
    """Dá baixa num prazo cumprido ('baixa o prazo da contestação').

    Casa as palavras da consulta contra processo+ato+resumo dos prazos
    abertos. Só baixa com casamento ÚNICO — ambiguidade devolve a lista
    para o usuário escolher (baixar prazo errado é pior que perguntar).
    """
    words = [w for w in _normalize(query).split() if len(w) >= 3]
    if not words:
        return "Diga qual prazo baixar (processo, ato ou parte do resumo)."
    matches = []
    for p in pending(days_ahead=365):
        text = _normalize(f"{p['processo']} {p['ato']} {p['resumo']}")
        if all(w in text for w in words):
            matches.append(p)
    if not matches:
        return (f"Nenhum prazo aberto casa com «{query}». " + speakable(30))
    if len(matches) > 1:
        opts = "; ".join(f"{p['data_limite']}: {p['resumo'] or p['ato']}"
                         for p in matches)
        return (f"Encontrei {len(matches)} prazos: {opts}. "
                "Qual deles devo baixar?")
    p = dict(matches[0])
    p["status"] = "baixado"
    p["baixado_em"] = time.strftime("%Y-%m-%d %H:%M")
    _append_prazo(p)          # última linha vence — baixa sem apagar histórico
    logger.info(f"radar: baixado — {p['resumo']}")
    return (f"Baixado: {p['resumo'] or p['ato']} "
            f"(vencia {p['data_limite']}). Bom trabalho, senhor.")


RADAR_TOOL_SCHEMA = {
    "name": "radar_prazos",
    "description": "Radar de prazos jurídicos: lista prazos processuais "
                   "pendentes ('quais meus prazos?', 'tenho prazo vencendo?'), "
                   "varre o e-mail atrás de intimações novas (action='scan') "
                   "ou dá baixa num prazo cumprido (action='baixar', 'já "
                   "protocolei a contestação, baixa o prazo').",
    "properties": {
        "action": {"type": "string",
                   "description": "'list' (padrão), 'scan' ou 'baixar'"},
        "days": {"type": "string",
                 "description": "horizonte em dias (padrão '15')"},
        "query": {"type": "string",
                  "description": "para 'baixar': processo, ato ou trecho que "
                                 "identifique o prazo"},
    },
    "required": [],
}


def radar_tool(args: dict) -> str:
    action = str(args.get("action", "list")).lower().strip()
    days = int(str(args.get("days") or 15))
    if action == "scan":
        found = scan()
        if not found:
            return ("Varredura concluída: nenhuma intimação nova. " +
                    speakable(days))
        news = "; ".join(f"{p['data_limite']}: {p['resumo']}" for p in found)
        return f"Varredura concluída, {len(found)} prazo(s) novo(s): {news}"
    if action == "baixar":
        return settle(str(args.get("query", "")))
    return speakable(days)
