"""
JARVIS v2 — TTS via Microsoft Edge (edge-tts) para o Pipecat.

Nasceu quando a cota gratuita do ElevenLabs zerou no meio do caminho
(2026-07-08: websocket fechava o contexto SEM áudio e o bot ficava mudo
"pensando"). O Edge é gratuito, tem o pt-BR-AntonioNeural — a MESMA voz
das notas de áudio do Telegram — e segura o serviço até a cota voltar.

Seleção em config/api_keys.json: tts_provider = "edge" | "elevenlabs"
(voz: edge_tts_voice). O edge-tts entrega MP3; um ffmpeg por frase
decodifica em streaming para PCM s16le mono na taxa do pipeline
(latência medida do primeiro áudio: ~0,5-0,8s — acima do ElevenLabs,
aceitável para fallback).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from loguru import logger

from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame
from pipecat.services.tts_service import TTSService


class EdgeTTSService(TTSService):
    def __init__(self, *, voice: str = "pt-BR-AntonioNeural",
                 rate: str = "+8%", **kwargs):
        super().__init__(**kwargs)
        self._voice = voice
        self._rate = rate          # Antonio nativo é lento p/ assistente

    def can_generate_metrics(self) -> bool:
        return True

    async def run_tts(self, text: str,
                      context_id: str) -> AsyncGenerator[Frame, None]:
        logger.debug(f"{self}: Generating TTS [{text}]")
        sr = self.sample_rate or 24000
        proc = None
        try:
            import edge_tts
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-loglevel", "quiet", "-f", "mp3", "-i", "pipe:0",
                "-f", "s16le", "-acodec", "pcm_s16le",
                "-ar", str(sr), "-ac", "1", "pipe:1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
            )

            async def _feed():
                """MP3 do Edge → stdin do ffmpeg (concorrente com a leitura,
                senão o pipe enche e trava os dois lados)."""
                try:
                    comm = edge_tts.Communicate(text, self._voice,
                                                rate=self._rate)
                    async for chunk in comm.stream():
                        if chunk["type"] == "audio" and chunk["data"]:
                            proc.stdin.write(chunk["data"])
                            await proc.stdin.drain()
                except Exception as e:
                    logger.warning(f"edge-tts stream falhou: {e}")
                finally:
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass

            feeder = asyncio.create_task(_feed())
            measuring = True
            try:
                while True:
                    pcm = await proc.stdout.read(4096)
                    if not pcm:
                        break
                    if measuring:
                        await self.stop_ttfb_metrics()
                        measuring = False
                    yield TTSAudioRawFrame(pcm, sr, 1, context_id=context_id)
            finally:
                feeder.cancel()
                try:
                    await feeder
                except (asyncio.CancelledError, Exception):
                    pass
            if measuring:
                # nada saiu do ffmpeg: rede/serviço indisponível
                yield ErrorFrame(error="EdgeTTS não produziu áudio")
        except Exception as e:
            logger.error(f"EdgeTTS falhou: {e}")
            yield ErrorFrame(error=f"EdgeTTS: {e}")
        finally:
            if proc and proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
