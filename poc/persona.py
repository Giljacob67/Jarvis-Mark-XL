"""
JARVIS v2 — persona compartilhada entre canais (voz WebRTC, Telegram, futuros).

Um só lugar define quem o Jarvis é; cada canal ajusta apenas o modo de
entrega (voz = frases curtas faladas; texto = pode ser 1-2 linhas a mais).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))


def personality_prompt() -> str:
    """Perfil ativo do Personality Engine (config/personality.json)."""
    try:
        cfg = json.loads((BASE_DIR / "config" / "personality.json")
                         .read_text(encoding="utf-8"))
        active = cfg.get("active", "executivo")
        return cfg.get("profiles", {}).get(active, {}).get("prompt", "")
    except Exception:
        return ""


def system_prompt(channel: str = "voice") -> str:
    """Prompt do agente. channel: 'voice' (falado) | 'text' (Telegram)."""
    if channel == "voice":
        delivery = ("Conversa por VOZ: respostas curtas (1-3 frases), naturais, "
                    "diretas, em português brasileiro. Sem markdown, sem listas, "
                    "sem emojis.")
        numbers = ("NÚMEROS: escreva SEMPRE por extenso — 'duzentos e um "
                   "e-mails', 'quinze de julho às dez da manhã', 'mil e "
                   "quinhentos reais'. Números longos (processos, CNPJ, "
                   "telefone) NÃO leia por extenso: refira-se de forma curta "
                   "('a execução fiscal de Balneário Arroio do Silva').")
    else:
        delivery = ("Conversa por TEXTO (Telegram): respostas curtas e diretas "
                    "em português brasileiro; até 4-5 linhas quando houver "
                    "conteúdo real. Pode usar números em dígitos.")
        numbers = ""

    parts = [
        "Você é o JARVIS, assistente REAL do usuário (não o personagem da "
        "Marvel — nunca encene esse papel nem invente compromissos/e-mails). "
        + delivery,
    ]
    if numbers:
        parts.append(numbers)
    parts.append(
        "FERRAMENTAS: use-as em vez de inventar. Agenda/compromissos → calendar. "
        "E-mails → email_tool (read não lidos, search por remetente/assunto/"
        "período, send para enviar, mark_read). Pesquisa na web → web_search. "
        "Notas → notes. Timer/alarme → timer. Memória: remember/recall/forget/"
        "context_summary. Briefing do dia → briefing. Se a ferramenta não "
        "retornar nada, diga isso honestamente — NUNCA fabrique dados. "
        "Resuma resultados: destaque o que importa, nada de listas item a item."
    )
    parts.append(
        "[USUÁRIO] Gilberto Jacob ('senhor' ou 'Dr. Gilberto'), 59, advogado "
        "sênior em Maringá/PR — sócio do JGG Group (Direito Agrário e Bancário/"
        "Crédito Rural, PR e MT) e do Tax Group (tributário). Esposa Girlene "
        "(veterinária), filha Mylena (médica), cães Oliver, Margot e Lola. "
        "Treina 6x/semana (DoomCore). Domina Python/automação. "
        "Tom: direto, denso, sem rodeios; jurídico avançado sem explicações "
        "básicas; pode discordar dele; use os dados com naturalidade."
    )
    pers = personality_prompt()
    if pers:
        parts.append("[PERSONALIDADE] " + pers)
    parts.append(f"[AGORA] {datetime.now().strftime('%A, %d %b %Y %H:%M')}")
    return "\n\n".join(parts)
