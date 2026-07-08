"""
JARVIS — Safety & Permissions Layer.

Toda execução de ferramenta passa por decide() antes e por audit() depois.

Riscos (sensíveis à AÇÃO, não só à ferramenta):
  low    — leitura/consulta (agenda list, e-mails read, clima, notas list)
  medium — muda estado reversível (criar evento, marcar lido, salvar nota)
  high   — irreversível ou de impacto externo (enviar e-mail/mensagem,
           apagar, executar código/shell, instalar, controlar mouse/teclado)

Modos (config "permission_mode", padrão "supervised"):
  read_only  — só risco baixo; o resto é negado com explicação
  supervised — alto risco exige confirmação VERBAL (o LLM pergunta ao
               usuário e re-chama a ferramenta com confirm='sim')
  autonomous — executa tudo, logando. DESLIGADO por padrão; opt-in
               explícito no config. Nunca ativado por voz.

Auditoria: memory/audit_log.jsonl — o que foi pedido, decisão, resultado.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from core.logger import get_logger

log = get_logger("permissions")

AUDIT_PATH = Path(__file__).resolve().parent.parent / "memory" / "audit_log.jsonl"

_CONFIRM_WORDS = {"sim", "yes", "true", "confirmo", "confirmado", "pode", "1"}

# Matriz de risco. Chave: ferramenta; valor: risco fixo (str) OU dict
# ação→risco com "*" como padrão da ferramenta.
RISK_MATRIX: dict[str, str | dict[str, str]] = {
    # consulta pura
    "web_search":      "low",
    "weather_report":  "low",
    "calendar":        {"list": "low", "*": "medium"},          # create=medium
    "email_tool":      {"read": "low", "search": "low",
                        "mark_read": "medium", "send": "high", "*": "medium"},
    "notes":           {"list": "low", "search": "low",
                        "clear": "high", "*": "medium"},         # add/delete=medium
    "timer":           {"list": "low", "*": "medium"},
    # memória (Fase 1)
    "briefing":        "low",
    # radar de prazos (Fase 4): scan lê Gmail e cria evento no Calendar
    "radar_prazos":    {"list": "low", "scan": "medium",
                        "baixar": "medium", "*": "low"},
    "screen_look":     "medium",   # privacidade: nunca 'low'; modo privado no satélite
    "remember":        "medium",
    "recall":          "low",
    "context_summary": "low",
    "forget":          "medium",
    # desktop / impacto externo (satélite ou G1)
    "open_app":          "low",
    "send_message":      "high",
    "file_controller":   {"read": "low", "list": "low",
                          "delete": "high", "trash": "high", "*": "medium"},
    "computer_control":  "high",
    "computer_settings": "medium",
    "desktop_control":   "high",
    "code_helper":       {"explain": "low", "*": "high"},
    "dev_agent":         "high",
    "app_installer":     "high",
    "clipboard":         "medium",
    "browser_control":   "medium",
}

_DEFAULT_RISK = "medium"   # ferramenta fora da matriz nunca é tratada como baixa


def risk_of(tool: str, args: dict | None = None) -> str:
    entry = RISK_MATRIX.get(tool, _DEFAULT_RISK)
    if isinstance(entry, str):
        return entry
    action = str((args or {}).get("action", "")).lower().strip()
    return entry.get(action, entry.get("*", _DEFAULT_RISK))


def is_confirmed(args: dict | None) -> bool:
    return str((args or {}).get("confirm", "")).lower().strip() in _CONFIRM_WORDS


def decide(tool: str, args: dict | None, mode: str = "supervised") -> tuple[str, str]:
    """Retorna (decisão, motivo): 'allow' | 'confirm' | 'deny'."""
    risk = risk_of(tool, args)
    mode = (mode or "supervised").lower().strip()

    if mode == "read_only":
        if risk == "low":
            return "allow", "leitura em modo somente-leitura"
        return "deny", f"modo somente-leitura ativo (risco {risk})"

    if mode == "autonomous":
        return "allow", f"modo autônomo (risco {risk}, auditado)"

    # supervised (padrão)
    if risk == "high" and not is_confirmed(args):
        return "confirm", f"risco alto ({tool}) exige confirmação verbal"
    return "allow", f"risco {risk} em modo supervisionado"


def confirmation_request(tool: str, args: dict | None) -> str:
    """Texto devolvido ao LLM quando a decisão é 'confirm' — instrui o fluxo
    de confirmação verbal sem quebrar a conversa."""
    resumo = ", ".join(f"{k}={str(v)[:40]}" for k, v in (args or {}).items()
                       if k not in ("confirm",))
    return (
        f"CONFIRMAÇÃO NECESSÁRIA: a ação '{tool}' ({resumo}) é de risco alto. "
        "Descreva ao usuário em uma frase o que será feito e pergunte se "
        "confirma. SOMENTE se ele confirmar verbalmente, chame novamente a "
        "mesma ferramenta com os mesmos parâmetros MAIS confirm='sim'. "
        "Se ele negar, não chame e diga que foi cancelado."
    )


# ── auditoria ────────────────────────────────────────────────────────────
_audit_lock = threading.Lock()


def audit(tool: str, args: dict | None, decision: str, result: str = "") -> None:
    """Registra toda decisão/execução em JSONL (nunca falha o chamador)."""
    entry = {
        "ts":       time.strftime("%Y-%m-%d %H:%M:%S"),
        "tool":     tool,
        "risk":     risk_of(tool, args),
        "decision": decision,
        "args":     {k: str(v)[:120] for k, v in (args or {}).items()},
        "result":   str(result)[:300],
    }
    try:
        with _audit_lock:
            AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(AUDIT_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning("auditoria falhou: %s", e)
