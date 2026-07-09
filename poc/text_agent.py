"""
JARVIS v2 — agente de TEXTO: o mesmo cérebro da voz, para Telegram e canais
futuros (e para a proatividade compor mensagens).

Mini-loop de function calling direto na API OpenAI-compatível (Cerebras,
fallback Groq), usando EXATAMENTE as mesmas ferramentas, permissões,
auditoria e memória do canal de voz (poc/tools_bridge).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from loguru import logger

from poc.persona import system_prompt

CFG = json.loads((BASE_DIR / "config" / "api_keys.json").read_text(encoding="utf-8"))

_MAX_ROUNDS = 5


def _client_and_model():
    from openai import OpenAI
    if CFG.get("cerebras_api_key", "").strip():
        return (OpenAI(api_key=CFG["cerebras_api_key"],
                       base_url="https://api.cerebras.ai/v1"), "gpt-oss-120b")
    return (OpenAI(api_key=CFG["groq_api_key"],
                   base_url="https://api.groq.com/openai/v1"),
            CFG.get("llm_model", "openai/gpt-oss-120b"))


def chat_once(messages: list[dict], max_tokens: int = 1500) -> str:
    """Uma completion SEM tools, resiliente: Cerebras (retry no 429/erro
    transitório) → Groq. Para fraseio de briefing/boletins — não para turnos."""
    import time as _time

    from openai import OpenAI
    attempts = [_client_and_model()]
    if CFG.get("groq_api_key", "").strip():
        attempts.append((OpenAI(api_key=CFG["groq_api_key"],
                                base_url="https://api.groq.com/openai/v1"),
                         CFG.get("llm_model", "openai/gpt-oss-120b")))
    last: Exception | None = None
    for i, (client, model) in enumerate(attempts):
        for retry in range(2):
            try:
                resp = client.chat.completions.create(
                    model=model, max_tokens=max_tokens, messages=messages)
                text = (resp.choices[0].message.content or "").strip()
                if text:
                    return text
            except Exception as e:
                last = e
                logger.warning(f"chat_once {model} tentativa {retry + 1}: {e}")
                _time.sleep(2)
    raise last or RuntimeError("chat_once: nenhum provedor respondeu")


def _openai_tools() -> list[dict]:
    """Ferramentas do bridge no formato OpenAI (mesma fonte da voz)."""
    from poc.tools_bridge import build_tools
    out = []
    for s in build_tools().standard_tools:
        out.append({"type": "function", "function": {
            "name": s.name, "description": s.description,
            "parameters": {"type": "object", "properties": s.properties,
                           "required": s.required},
        }})
    return out


async def _exec_tool(name: str, args: dict) -> str:
    """Executa via o MESMO caminho da voz: permissões → dispatch → auditoria."""
    from poc.tools_bridge import _handle

    class _P:
        function_name = name
        arguments = args
        result = ""
        async def result_callback(self, r):  # noqa: N802 (API do Pipecat)
            self.result = r

    p = _P()
    await _handle(p)
    return p.result


def run_turn(user_text: str, history: list[dict] | None = None) -> str:
    """Um turno completo de texto (síncrono — chamado de threads de serviço)."""
    client, model = _client_and_model()
    tools = _openai_tools()
    messages = [{"role": "system", "content": system_prompt("text")}]
    messages += (history or [])[-12:]
    messages.append({"role": "user", "content": user_text})

    for _ in range(_MAX_ROUNDS):
        resp = client.chat.completions.create(
            model=model, messages=messages, tools=tools, max_tokens=1500,
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return (msg.content or "").strip() or "Feito, senhor."

        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            logger.info(f"[texto] 🔧 {tc.function.name} {args}")
            result = asyncio.run(_exec_tool(tc.function.name, args))
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": str(result)[:2000]})

    return "A tarefa ficou extensa demais para um turno — pode reformular?"
