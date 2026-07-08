"""
JARVIS v2 — Ponte headless: actions existentes → function calling do Pipecat.

As actions foram escritas para o app Qt (recebem player=ui, speak=callback).
Aqui elas rodam sem UI: um shim de logger no lugar do player, e um hook
say() que injeta fala espontânea no pipeline (usado pelo timer ao disparar).

Fase 2 — ferramentas de maior valor primeiro:
    calendar, email_tool, web_search, notes, timer, open_app
As demais entram por adição na _SELECTED (o schema vem de core/tools.py,
a mesma fonte usada pelo app antigo — uma definição só).
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

# Ferramentas expostas nesta fase (nome → callable(parameters, ...) -> str)
_SELECTED = ("calendar", "email_tool", "web_search", "notes", "timer", "open_app")

# Ferramentas de desktop em servidor headless: se o SATÉLITE estiver
# configurado (satellite_url no config), elas rodam por RPC na tailnet;
# sem satélite E sem DISPLAY, saem do schema.
import json as _json
import os as _os

_DESKTOP_ONLY = {"open_app"}


def _satellite_cfg() -> tuple[str, str]:
    try:
        cfg = _json.loads((BASE_DIR / "config" / "api_keys.json").read_text())
        return cfg.get("satellite_url", ""), cfg.get("satellite_token", "")
    except Exception:
        return "", ""


_SAT_URL, _SAT_TOKEN = _satellite_cfg()
_HAS_DISPLAY = bool(_os.environ.get("DISPLAY") or _os.environ.get("WAYLAND_DISPLAY"))
if not _HAS_DISPLAY and not _SAT_URL:
    _SELECTED = tuple(t for t in _SELECTED if t not in _DESKTOP_ONLY)


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


def _dispatch(name: str, args: dict) -> str:
    """Executa a action (bloqueante) — mesma rota do main.py, sem Qt."""
    ui = HeadlessUI()
    # Ferramentas de memória (Fase 1) — locais, sem action externa
    if name == "screen_look":
        if not _SAT_URL:
            return "Visão de tela indisponível: satélite não configurado."
        return _satellite_call("/screen_look",
                               {"question": args.get("question", "")})
    if name == "open_app" and not _HAS_DISPLAY and _SAT_URL:
        return _satellite_call("/open_app",
                               {"app_name": args.get("app_name", "")}, timeout=30)
    if name == "briefing":
        from poc.briefing import generate
        return generate(mode=args.get("mode", "medio"),
                        question=args.get("question"))
    if name == "radar_prazos":
        from poc.radar import radar_tool
        return radar_tool(args)
    if name == "status_sistema":
        from core.health import speakable as health_speakable
        return health_speakable()
    if name in ("remember", "recall", "forget", "context_summary"):
        from memory.layered import get_memory
        m = get_memory()
        if name == "remember":
            return m.remember(args.get("text", ""))
        if name == "recall":
            return m.recall(args.get("query", ""))
        if name == "forget":
            return m.forget(args.get("query", ""))
        return m.context_summary()
    if name == "calendar":
        from actions.calendar_tool import calendar_tool
        return calendar_tool(parameters=args, player=ui) or "Feito."
    if name == "email_tool":
        from actions.email_tool import email_tool
        return email_tool(parameters=args, player=ui, speak=_say) or "Feito."
    if name == "web_search":
        from actions.web_search import web_search
        return web_search(parameters=args, player=ui) or "Feito."
    if name == "notes":
        from actions.notes_tool import notes_tool
        return notes_tool(parameters=args, player=ui, speak=_say) or "Feito."
    if name == "timer":
        from actions.timer_tool import timer_tool
        return timer_tool(parameters=args, player=ui, speak=_say) or "Feito."
    if name == "open_app":
        from actions.open_app import open_app
        r = open_app(parameters=args, response=None, player=ui)
        return r or f"Abri {args.get('app_name', 'o aplicativo')}."
    return f"Ferramenta desconhecida: {name}"


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
    """Schemas de core/tools.py (fonte única) → FunctionSchema com handler."""
    from core.tools import OLLAMA_TOOLS

    schemas = []
    for t in OLLAMA_TOOLS:
        fn = t["function"]
        if fn["name"] not in _SELECTED:
            continue
        params = fn.get("parameters", {})
        # Groq valida os argumentos contra o schema ESTRITAMENTE e o Llama
        # costuma emitir números/booleanos como texto ('5', 'true') — a
        # completion inteira era rejeitada e o bot ficava mudo.  Todos os
        # parâmetros viram string no schema; as actions já coagem os tipos.
        props = {}
        for k, v in (params.get("properties") or {}).items():
            nv = dict(v)
            if nv.get("type") in ("integer", "number", "boolean"):
                nv["type"] = "string"
                nv["description"] = (nv.get("description", "") +
                                     " (valor como texto, ex: '5' ou 'true')").strip()
            props[k] = nv
        # ferramentas com ações de risco alto ganham o parâmetro de
        # confirmação verbal (fluxo do core/permissions.py)
        from core.permissions import RISK_MATRIX
        entry = RISK_MATRIX.get(fn["name"])
        if entry == "high" or (isinstance(entry, dict) and "high" in entry.values()):
            props["confirm"] = {
                "type": "string",
                "description": "Envie 'sim' SOMENTE após o usuário confirmar "
                               "verbalmente uma ação de risco alto.",
            }
        schemas.append(FunctionSchema(
            name=fn["name"],
            description=fn["description"],
            properties=props,
            required=params.get("required", []),
            handler=_handle,
        ))

    # Ferramentas de memória (Fase 1) + briefing (Fase 2) + visão (satélite)
    from memory.layered import MEMORY_TOOL_SCHEMAS
    from poc.briefing import BRIEFING_TOOL_SCHEMA
    from core.health import HEALTH_TOOL_SCHEMA
    from poc.radar import RADAR_TOOL_SCHEMA
    extra_schemas = [*MEMORY_TOOL_SCHEMAS, BRIEFING_TOOL_SCHEMA,
                     RADAR_TOOL_SCHEMA, HEALTH_TOOL_SCHEMA]
    if _SAT_URL or _HAS_DISPLAY:
        extra_schemas.append({
            "name": "screen_look",
            "description": "Olha a TELA DO COMPUTADOR do usuário e responde "
                           "('o que estou vendo?', 'me ajude com essa tela', "
                           "'explique esse erro', 'qual o próximo passo aqui?').",
            "properties": {"question": {"type": "string",
                           "description": "O que o usuário quer saber da tela"}},
            "required": [],
        })
    for ms in extra_schemas:
        schemas.append(FunctionSchema(
            name=ms["name"], description=ms["description"],
            properties=ms["properties"], required=ms["required"],
            handler=_handle,
        ))

    logger.info(f"Ferramentas ativas: {[s.name for s in schemas]}")
    return ToolsSchema(standard_tools=schemas)
