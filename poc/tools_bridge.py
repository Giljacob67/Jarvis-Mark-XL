"""
JARVIS v2 — Ponte headless: actions existentes → function calling do Pipecat.

As actions foram escritas para o app Qt (recebem player=ui, speak=callback).
Aqui elas rodam sem UI: um shim de logger no lugar do player, e um hook
say() que injeta fala espontânea no pipeline (usado pelo timer ao disparar).

A lista de ferramentas, schemas e handlers vive no REGISTRY UNIFICADO
(core/registry.py) — este módulo só faz a ponte para o Pipecat: contexto
de ambiente (satélite/display/say), coerção de tipos p/ Groq/Cerebras,
parâmetro de confirmação de risco alto, permissões e auditoria.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Callable

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from loguru import logger

from pipecat.processors.aggregators.llm_context import FunctionSchema, ToolsSchema

import json as _json
import os as _os


def _satellite_cfg() -> tuple[str, str]:
    try:
        cfg = _json.loads((BASE_DIR / "config" / "api_keys.json").read_text())
        return cfg.get("satellite_url", ""), cfg.get("satellite_token", "")
    except Exception:
        return "", ""


_SAT_URL, _SAT_TOKEN = _satellite_cfg()
_HAS_DISPLAY = bool(_os.environ.get("DISPLAY") or _os.environ.get("WAYLAND_DISPLAY"))


def _satellite_call(endpoint: str, payload: dict, timeout: int = 120) -> str:
    import requests
    try:
        r = requests.post(f"{_SAT_URL}{endpoint}", json=payload,
                          headers={"X-Jarvis-Token": _SAT_TOKEN},
                          timeout=timeout)
        if r.status_code != 200:
            return f"Satélite respondeu {r.status_code}."
        return r.json().get("result", "Feito.")
    except requests.exceptions.ConnectionError:
        return ("O computador do escritório parece desligado — o satélite "
                "não respondeu.")
    except Exception as e:
        return f"Falha no satélite: {e}"


class HeadlessUI:
    """Substitui o JarvisUI para actions que só usam write_log/estado."""
    muted = False
    current_file = None

    def write_log(self, text: str) -> None:
        logger.info(f"[tool] {text}")

    def set_state(self, state: str) -> None:
        pass


# Hook de fala espontânea — o bot.py conecta isso ao pipeline (TTSSpeakFrame).
_say_hook: Callable[[str], None] | None = None


def set_say_hook(fn: Callable[[str], None]) -> None:
    global _say_hook
    _say_hook = fn


def _say(text: str) -> None:
    if _say_hook and text:
        try:
            _say_hook(text)
        except Exception as e:
            logger.warning(f"say hook falhou: {e}")


def _ctx() -> dict:
    """Ambiente do processo, na forma que o registry espera."""
    return {"ui": HeadlessUI(), "say": _say,
            "satellite": _satellite_call if _SAT_URL else None,
            "has_display": _HAS_DISPLAY}


def _dispatch(name: str, args: dict) -> str:
    """Executa a ferramenta (bloqueante) — lookup no registry unificado."""
    from core.registry import dispatch
    return dispatch(name, args, _ctx())


# Ferramentas lentas ganham backchannel ("Um momento.") — com moderação:
# só onde a latência real justifica, e frase curta única.
_SLOW_TOOLS = {"web_search", "email_tool", "radar_prazos"}
_BACKCHANNEL = ("Um momento.", "Já verifico.", "Verificando.")
_bc_i = 0


async def _handle(params) -> None:
    """Handler async do Pipecat — permissões → backchannel → execução →
    auditoria + memória operacional. Action roda em thread (não bloqueia áudio)."""
    import json as _json

    from core.permissions import audit, confirmation_request, decide
    from core.presence import get_presence
    from memory.layered import get_memory

    name = params.function_name
    args = params.arguments or {}
    presence, mem = get_presence(), get_memory()
    logger.info(f"🔧 {name} {args}")

    # ── Safety & Permissions Layer ────────────────────────────────────────
    mode = _permission_mode()
    decision, reason = decide(name, args, mode)
    if decision == "deny":
        audit(name, args, "deny", reason)
        await params.result_callback(
            f"AÇÃO BLOQUEADA ({reason}). Informe o usuário educadamente.")
        return
    if decision == "confirm":
        audit(name, args, "confirm_requested", reason)
        await params.result_callback(confirmation_request(name, args))
        return

    if name == "screen_look":
        from core.presence import PresenceState
        presence.transition(PresenceState.OBSERVING_SCREEN, "capturando tela")
    else:
        presence.executing(name)
    global _bc_i
    if name in _SLOW_TOOLS and _say_hook:
        _say(_BACKCHANNEL[_bc_i % len(_BACKCHANNEL)])
        _bc_i += 1

    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, _dispatch, name, args
        )
        audit(name, args, "executed", str(result)[:120])
    except Exception as e:
        logger.error(f"{name} falhou: {e}")
        result = f"A ferramenta {name} encontrou um erro: {e}"
        audit(name, args, "error", str(e)[:120])
        presence.error(f"{name}: {e}")

    # memória operacional + episódica (só ações que mudam estado)
    mem.op_action(f"{name}({_json.dumps(args, ensure_ascii=False)[:60]})")
    from core.permissions import risk_of
    if risk_of(name, args) in ("medium", "high") and name != "remember":
        mem.remember(f"Executei {name}: {str(result)[:100]}", kind="tool")

    presence.thinking("pós-ferramenta")
    await params.result_callback(str(result)[:2000])


def _permission_mode() -> str:
    try:
        import json as _json
        cfg = _json.loads((BASE_DIR / "config" / "api_keys.json").read_text())
        return cfg.get("permission_mode", "supervised")
    except Exception:
        return "supervised"


def build_tools() -> ToolsSchema:
    """Registry unificado → FunctionSchema com handler do Pipecat."""
    from core.permissions import RISK_MATRIX
    from core.registry import tools_for

    schemas = []
    for tool in tools_for(_ctx()):
        s = tool.schema()
        # Groq valida os argumentos contra o schema ESTRITAMENTE e o Llama
        # costuma emitir números/booleanos como texto ('5', 'true') — a
        # completion inteira era rejeitada e o bot ficava mudo.  Todos os
        # parâmetros viram string no schema; as actions já coagem os tipos.
        props = {}
        for k, v in s["properties"].items():
            nv = dict(v)
            if nv.get("type") in ("integer", "number", "boolean"):
                nv["type"] = "string"
                nv["description"] = (nv.get("description", "") +
                                     " (valor como texto, ex: '5' ou 'true')").strip()
            props[k] = nv
        # ferramentas com ações de risco alto ganham o parâmetro de
        # confirmação verbal (fluxo do core/permissions.py)
        entry = RISK_MATRIX.get(s["name"])
        if entry == "high" or (isinstance(entry, dict) and "high" in entry.values()):
            props["confirm"] = {
                "type": "string",
                "description": "Envie 'sim' SOMENTE após o usuário confirmar "
                               "verbalmente uma ação de risco alto.",
            }
        schemas.append(FunctionSchema(
            name=s["name"], description=s["description"],
            properties=props, required=s["required"],
            handler=_handle,
        ))

    logger.info(f"Ferramentas ativas: {[s.name for s in schemas]}")
    return ToolsSchema(standard_tools=schemas)
