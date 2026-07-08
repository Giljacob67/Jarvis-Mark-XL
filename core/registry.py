"""
JARVIS v2 — Registry unificado de ferramentas (Fase 2, aposenta o
dispatcher em cadeia).

FONTE ÚNICA: cada ferramenta viva do JARVIS é uma entrada aqui — schema
(descrição/parâmetros) e handler juntos, no mesmo lugar. Consumidores:

  poc/tools_bridge.py  voz (Pipecat) e texto (text_agent): converte as
                       entradas em FunctionSchema e despacha por lookup
  core/permissions.py  risco por nome/ação (RISK_MATRIX) — segue separado
                       de propósito: segurança não mora junto do handler

Para os schemas herdados da G1 (core/tools.py, TOOL_DECLARATIONS) a
entrada referencia o nome e o registry puxa de lá — uma definição só,
como sempre. Ferramentas novas (memória, briefing, radar, saúde) trazem
o schema inline.

Handlers: fn(args: dict, ctx: dict) -> str, imports preguiçosos (nada de
Qt/Pipecat no import). ctx carrega o que é do AMBIENTE do chamador:
  ui          player headless p/ actions da G1 (write_log)
  say         fala espontânea no pipeline (ou no-op)
  satellite   RPC no desktop via tailnet (ou None)
  has_display DISPLAY/WAYLAND presente no processo

`available(ctx)` tira do schema o que não faz sentido no ambiente (ex.:
open_app em servidor sem satélite). Adicionar ferramenta = uma entrada
em TOOLS (+ risco na RISK_MATRIX se não for 'medium', o default seguro).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from core.logger import get_logger

log = get_logger("registry")


@dataclass(frozen=True)
class Tool:
    name: str
    run: Callable[[dict, dict], str]
    # schema inline (ferramentas novas)…
    description: str = ""
    properties: dict = field(default_factory=dict)
    required: list = field(default_factory=list)
    # …ou herdado da G1 (core/tools.py) quando from_g1=True
    from_g1: bool = False
    available: Callable[[dict], bool] = lambda ctx: True

    def schema(self) -> dict:
        """{name, description, properties, required} — resolve G1 se preciso."""
        if not self.from_g1:
            return {"name": self.name, "description": self.description,
                    "properties": self.properties, "required": self.required}
        from core.tools import OLLAMA_TOOLS
        for t in OLLAMA_TOOLS:
            fn = t["function"]
            if fn["name"] == self.name:
                params = fn.get("parameters", {})
                return {"name": self.name,
                        "description": fn["description"],
                        "properties": params.get("properties") or {},
                        "required": params.get("required", [])}
        raise KeyError(f"schema G1 ausente para '{self.name}'")


# ── handlers (imports preguiçosos; corpo idêntico ao dispatcher antigo) ───

def _calendar(args, ctx):
    from actions.calendar_tool import calendar_tool
    return calendar_tool(parameters=args, player=ctx["ui"]) or "Feito."


def _email(args, ctx):
    from actions.email_tool import email_tool
    return email_tool(parameters=args, player=ctx["ui"],
                      speak=ctx["say"]) or "Feito."


def _web_search(args, ctx):
    from actions.web_search import web_search
    return web_search(parameters=args, player=ctx["ui"]) or "Feito."


def _notes(args, ctx):
    from actions.notes_tool import notes_tool
    return notes_tool(parameters=args, player=ctx["ui"],
                      speak=ctx["say"]) or "Feito."


def _timer(args, ctx):
    from actions.timer_tool import timer_tool
    return timer_tool(parameters=args, player=ctx["ui"],
                      speak=ctx["say"]) or "Feito."


def _open_app(args, ctx):
    # servidor headless com satélite: roda no desktop via tailnet
    if not ctx.get("has_display") and ctx.get("satellite"):
        return ctx["satellite"]("/open_app",
                                {"app_name": args.get("app_name", "")},
                                timeout=30)
    from actions.open_app import open_app
    r = open_app(parameters=args, response=None, player=ctx["ui"])
    return r or f"Abri {args.get('app_name', 'o aplicativo')}."


def _screen_look(args, ctx):
    if not ctx.get("satellite"):
        return "Visão de tela indisponível: satélite não configurado."
    return ctx["satellite"]("/screen_look",
                            {"question": args.get("question", "")})


def _briefing(args, ctx):
    from poc.briefing import generate
    return generate(mode=args.get("mode", "medio"),
                    question=args.get("question"))


def _radar(args, ctx):
    from poc.radar import radar_tool
    return radar_tool(args)


def _status(args, ctx):
    from core.health import speakable
    return speakable()


def _mem(method: str):
    def run(args, ctx):
        from memory.layered import get_memory
        m = get_memory()
        if method == "remember":
            return m.remember(args.get("text", ""))
        if method == "recall":
            return m.recall(args.get("query", ""))
        if method == "forget":
            return m.forget(args.get("query", ""))
        return m.context_summary()
    return run


def _desktopish(ctx) -> bool:
    return bool(ctx.get("has_display") or ctx.get("satellite"))


def _build() -> dict[str, Tool]:
    from core.health import HEALTH_TOOL_SCHEMA
    from memory.layered import MEMORY_TOOL_SCHEMAS
    from poc.briefing import BRIEFING_TOOL_SCHEMA
    from poc.radar import RADAR_TOOL_SCHEMA

    tools = [
        # herdadas da G1 (schema em core/tools.py)
        Tool("calendar", _calendar, from_g1=True),
        Tool("email_tool", _email, from_g1=True),
        Tool("web_search", _web_search, from_g1=True),
        Tool("notes", _notes, from_g1=True),
        Tool("timer", _timer, from_g1=True),
        Tool("open_app", _open_app, from_g1=True, available=_desktopish),
        # nativas da v2 (schema inline nos módulos donos)
        Tool("screen_look", _screen_look, available=_desktopish,
             description="Olha a TELA DO COMPUTADOR do usuário e responde "
                         "('o que estou vendo?', 'me ajude com essa tela', "
                         "'explique esse erro', 'qual o próximo passo aqui?').",
             properties={"question": {"type": "string",
                         "description": "O que o usuário quer saber da tela"}}),
    ]
    handlers = {"briefing": _briefing, "radar_prazos": _radar,
                "status_sistema": _status,
                "remember": _mem("remember"), "recall": _mem("recall"),
                "forget": _mem("forget"),
                "context_summary": _mem("context_summary")}
    for s in [*MEMORY_TOOL_SCHEMAS, BRIEFING_TOOL_SCHEMA,
              RADAR_TOOL_SCHEMA, HEALTH_TOOL_SCHEMA]:
        tools.append(Tool(s["name"], handlers[s["name"]],
                          description=s["description"],
                          properties=s["properties"],
                          required=s.get("required", [])))
    return {t.name: t for t in tools}


_registry: dict[str, Tool] | None = None


def all_tools() -> dict[str, Tool]:
    global _registry
    if _registry is None:
        _registry = _build()
    return _registry


def tools_for(ctx: dict) -> list[Tool]:
    """As ferramentas que existem NESTE ambiente (satélite, display…)."""
    return [t for t in all_tools().values() if t.available(ctx)]


def dispatch(name: str, args: dict, ctx: dict) -> str:
    t = all_tools().get(name)
    if t is None:
        return f"Ferramenta desconhecida: {name}"
    return t.run(args, ctx)
