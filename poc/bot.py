"""
JARVIS v2 — Prova de conceito da nova camada de voz (Pipecat).

O que este POC demonstra vs. o pipeline atual:
  * mic = navegador via WebRTC → AEC/supressão de ruído nativos (Chrome)
  * Silero VAD local + interrupção (barge-in) LIGADA POR DEFAULT
  * frases fluem STT→LLM→TTS em streaming de frames, sem gates artesanais
  * mesma persona/perfil do Jarvis (memory/user_profile.md)

Rodar:
    cd ~/Jarvis-Mark-XL
    .venv-pipecat/bin/python poc/bot.py --transport webrtc --host 0.0.0.0

Abrir http://localhost:7860 no Chrome, permitir o microfone e conversar.
(Ferramentas/ações ficam para a Fase 2 — aqui o alvo é FLUIDEZ.)
"""
import json
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.transports.base_transport import BaseTransport, TransportParams

CFG = json.loads((BASE_DIR / "config" / "api_keys.json").read_text(encoding="utf-8"))


def _system_prompt() -> str:
    parts = [
        "Você é o JARVIS, assistente de voz REAL do usuário (não o personagem "
        "da Marvel — nunca encene esse papel nem invente compromissos/e-mails). "
        "Conversa por VOZ: respostas curtas (1-3 frases), naturais, diretas, "
        "em português brasileiro. Sem markdown, sem listas, sem emojis.",
        "FERRAMENTAS: use-as em vez de inventar. Agenda/compromissos → calendar. "
        "E-mails → email_tool (read para não lidos, search por remetente/assunto/"
        "período, send para enviar, mark_read para marcar lidos). Pesquisa na web "
        "→ web_search. Notas → notes. Timer/alarme → timer. Abrir aplicativo ou "
        "site → open_app. Se a ferramenta não retornar nada, diga isso honestamente "
        "— NUNCA fabrique dados. Ao falar resultados, resuma para voz: nada de ler "
        "listas longas item a item, destaque o que importa.",
    ]
    # Perfil COMPACTO: o tier gratuito do Groq tem 8k tokens/min — o perfil
    # completo (~1.5k tokens) + schemas estourava o limite em 2-3 turnos
    # (429 → resposta atrasada ~1min). Versão de voz: só o essencial.
    parts.append(
        "[USUÁRIO] Gilberto Jacob ('senhor' ou 'Dr. Gilberto'), 59, advogado "
        "sênior em Maringá/PR — sócio do JGG Group (Direito Agrário e Bancário/"
        "Crédito Rural, PR e MT) e do Tax Group (tributário). Esposa Girlene "
        "(veterinária), filha Mylena (médica), cães Oliver, Margot e Lola. "
        "Treina 6x/semana (DoomCore). Domina Python/automação. "
        "Tom: direto, denso, sem rodeios; jurídico avançado sem explicações "
        "básicas; pode discordar dele; use os dados com naturalidade."
    )
    parts.append(f"[AGORA] {datetime.now().strftime('%A, %d %b %Y %H:%M')}")
    return "\n\n".join(parts)


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    logger.info("JARVIS v2 POC — montando pipeline")

    stt = DeepgramSTTService(
        api_key=CFG["deepgram_api_key"],
        settings=DeepgramSTTService.Settings(
            model=CFG.get("deepgram_model", "nova-2"),
            language="pt-BR",
            # boost da wake word — sem isso vira 'Jermes'/'Ô gás'
            extra={"keywords": ["jarvis:5"]},
        ),
    )

    tts = ElevenLabsTTSService(
        api_key=CFG["elevenlabs_api_key"],
        settings=ElevenLabsTTSService.Settings(
            voice=CFG.get("tts_voice", "GIuLCSVfgJaUuh7hYOY8"),
            model=CFG.get("tts_model", "eleven_turbo_v2_5"),
            language="pt",
        ),
    )

    # gpt-oss-120b: tool calling disciplinado (o llama-3.3 emite chamadas
    # como TEXTO '<function=...>' no meio da fala e envenena o histórico).
    # Provedor: Cerebras quando a chave existe (medido: 0.79s p/ 1º token,
    # cota 30-60k tok/min vs 8k do Groq free — que dava 429 em 2-3 turnos);
    # Groq como fallback.
    if CFG.get("cerebras_api_key", "").strip():
        from pipecat.services.cerebras.llm import CerebrasLLMService
        llm = CerebrasLLMService(
            api_key=CFG["cerebras_api_key"],
            model="gpt-oss-120b",
        )
        logger.info("LLM: Cerebras gpt-oss-120b")
    else:
        llm = GroqLLMService(
            api_key=CFG["groq_api_key"],
            model=CFG.get("llm_model", "openai/gpt-oss-120b"),
        )
        logger.info("LLM: Groq gpt-oss-120b")

    from poc.tools_bridge import build_tools, set_say_hook

    context = LLMContext(tools=build_tools())
    context.add_message({"role": "system", "content": _system_prompt()})

    # Modo seguro contra loop de eco: JARVIS_NO_BARGE_IN=1 silencia o STT
    # enquanto o bot fala (sem interrupção, mas imune a eco de caixas).
    import os
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.turns.user_start import MinWordsUserTurnStartStrategy
    from pipecat.turns.user_turn_strategies import UserTurnStrategies

    # Interrupção exige ≥3 PALAVRAS transcritas enquanto o bot fala — eco
    # residual das caixas dispara o VAD mas não vira palavras, então parou
    # de cortar as respostas no meio ('ficou meio louco'). Com o bot calado
    # a estratégia exige só 1 palavra (comportamento normal preservado).
    user_params_kwargs: dict = {
        "vad_analyzer": SileroVADAnalyzer(params=VADParams(stop_secs=1.0)),
        "user_turn_strategies": UserTurnStrategies(
            start=[MinWordsUserTurnStartStrategy(min_words=3)],
        ),
    }
    if os.environ.get("JARVIS_NO_BARGE_IN") == "1":
        from pipecat.turns.user_mute import AlwaysUserMuteStrategy
        user_params_kwargs["user_mute_strategies"] = [AlwaysUserMuteStrategy()]
        logger.info("Modo NO-BARGE-IN: STT mudo enquanto o bot fala")

    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(**user_params_kwargs),
    )

    pipeline = Pipeline([
        transport.input(),
        stt,
        user_aggregator,
        llm,
        tts,
        transport.output(),
        assistant_aggregator,
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True),
    )

    # Fala espontânea vinda das actions (ex.: timer disparando) — injeta
    # direto no TTS do pipeline, de qualquer thread.
    import asyncio as _aio
    _loop = _aio.get_running_loop()

    def _say(text: str) -> None:
        _aio.run_coroutine_threadsafe(
            task.queue_frames([TTSSpeakFrame(text)]), _loop
        )

    set_say_hook(_say)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Cliente conectado — saudando")
        context.add_message({
            "role": "user",
            "content": "Cumprimente o usuário em uma frase curta e diga que esta "
                       "é a nova voz de testes.",
        })
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Cliente desconectou")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)
    await runner.run(task)


async def bot(runner_args: RunnerArguments):
    transport_params = {
        "webrtc": lambda: TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        ),
    }
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
