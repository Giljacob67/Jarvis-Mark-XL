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


async def _handle(params) -> None:
    """Handler async do Pipecat — roda a action em thread (não bloqueia áudio)."""
    name = params.function_name
    args = params.arguments or {}
    logger.info(f"🔧 {name} {args}")
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, _dispatch, name, args
        )
    except Exception as e:
        logger.error(f"{name} falhou: {e}")
        result = f"A ferramenta {name} encontrou um erro: {e}"
    await params.result_callback(str(result)[:2000])


def build_tools() -> ToolsSchema:
    """Schemas de core/tools.py (fonte única) → FunctionSchema com handler."""
    from core.tools import OLLAMA_TOOLS

    schemas = []
    for t in OLLAMA_TOOLS:
        fn = t["function"]
        if fn["name"] not in _SELECTED:
            continue
        params = fn.get("parameters", {})
        schemas.append(FunctionSchema(
            name=fn["name"],
            description=fn["description"],
            properties=params.get("properties", {}),
            required=params.get("required", []),
            handler=_handle,
        ))
    logger.info(f"Ferramentas ativas: {[s.name for s in schemas]}")
    return ToolsSchema(standard_tools=schemas)
