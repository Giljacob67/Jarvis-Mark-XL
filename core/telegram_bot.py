"""
MARK XL — Bot Telegram: canal remoto de qualquer lugar (texto e voz).

Fluxo:
  texto do usuário  → jarvis._process_message() → resposta agregada de volta
  voice note (OGG)  → ffmpeg → PCM 16k → STT → mesmo fluxo
  resposta          → mensagem de texto + (opcional) áudio MP3 via EdgeTTS

Segurança:
  - Só o dono fala com o bot. O primeiro usuário a mandar mensagem é
    "reivindicado" como dono (telegram_owner_id salvo no config) — crie o
    bot e mande /start imediatamente. Qualquer outro usuário é ignorado.
  - Token e owner ficam em config/api_keys.json (gitignored).

Implementação em HTTP puro (long polling getUpdates) — sem dependências novas.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import requests

from core.logger import get_logger
from core.paths import API_CONFIG_PATH

log = get_logger("telegram")


def _save_owner(owner_id: int) -> None:
    try:
        cfg = json.loads(API_CONFIG_PATH.read_text(encoding="utf-8"))
        cfg["telegram_owner_id"] = owner_id
        API_CONFIG_PATH.write_text(
            json.dumps(cfg, indent=4, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        log.error("Falha ao salvar telegram_owner_id: %s", e)


class TelegramBot:
    def __init__(self, jarvis):
        self._j       = jarvis
        self._token   = (jarvis._config.get("telegram_bot_token") or "").strip()
        self._api     = f"https://api.telegram.org/bot{self._token}"
        self._offset  = 0
        self._capture: list[str] | None = None   # buffer de speak() durante um turno
        self._thread: threading.Thread | None = None

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self) -> None:
        if not self._token:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="telegram")
        self._thread.start()
        log.info("Bot Telegram ativo")

    def _owner(self) -> int | None:
        v = self._j._config.get("telegram_owner_id")
        return int(v) if v else None

    # ── captura de respostas faladas ─────────────────────────────────────
    def capture_speech(self, text: str) -> None:
        """Chamado por JarvisLocal.speak() quando _speak_target == 'telegram'."""
        if self._capture is not None:
            self._capture.append(text)

    # ── long polling ─────────────────────────────────────────────────────
    def _loop(self) -> None:
        while True:
            try:
                resp = requests.get(
                    f"{self._api}/getUpdates",
                    params={"offset": self._offset, "timeout": 50},
                    timeout=60,
                ).json()
                for upd in resp.get("result", []):
                    self._offset = upd["update_id"] + 1
                    try:
                        self._handle(upd)
                    except Exception as e:
                        log.error("update falhou: %s", e)
            except requests.exceptions.RequestException:
                time.sleep(5)          # rede caiu — tenta de novo
            except Exception as e:
                log.error("poll: %s", e)
                time.sleep(5)

    # ── tratamento de mensagens ──────────────────────────────────────────
    def _handle(self, upd: dict) -> None:
        msg = upd.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        user_id = (msg.get("from") or {}).get("id")
        if not chat_id or not user_id:
            return

        owner = self._owner()
        if owner is None:
            # Primeiro contato reivindica o bot (crie e use imediatamente).
            _save_owner(user_id)
            self._j._config["telegram_owner_id"] = user_id
            name = (msg.get("from") or {}).get("first_name", "")
            self._j.ui.write_log(f"SYS: 📱 Telegram vinculado a {name} (id {user_id})")
            self._send_text(chat_id, "Vinculado, senhor. Este bot agora só responde a você. "
                                     "Mande texto ou áudio.")
            owner = user_id
            if msg.get("text", "").strip().lower() in ("/start", "start"):
                return
        if user_id != owner:
            log.warning("Mensagem de não-dono ignorada (id %s)", user_id)
            return

        text = (msg.get("text") or "").strip()
        if msg.get("voice"):
            text = self._transcribe_voice(msg["voice"], chat_id)
            if not text:
                return
            self._send_text(chat_id, f"🎤 «{text}»")

        if not text or text.startswith("/start"):
            if text.startswith("/start"):
                self._send_text(chat_id, "Às ordens, senhor.")
            return

        self._j.ui.write_log(f"[Telegram]: {text}")
        self._run_turn(chat_id, text)

    def _run_turn(self, chat_id: int, text: str) -> None:
        """Roda o turno com as respostas capturadas e devolve ao Telegram."""
        self._capture = []
        self._j._speak_target = "telegram"
        try:
            self._j._process_message(text, from_voice=False)
        except Exception as e:
            log.error("turno falhou: %s", e)
        finally:
            self._j._speak_target = "pc"
            parts, self._capture = self._capture, None

        reply = " ".join(p.strip() for p in parts if p and p.strip()).strip()
        if not reply:
            reply = "Feito, senhor."
        self._send_text(chat_id, reply)
        if self._j._config.get("telegram_voice_replies", True):
            self._send_tts_audio(chat_id, reply)

    # ── voz: entrada (OGG/Opus → STT) ────────────────────────────────────
    def _transcribe_voice(self, voice: dict, chat_id: int) -> str:
        if self._j._stt is None:
            self._send_text(chat_id, "STT ainda carregando — tente em instantes.")
            return ""
        try:
            info = requests.get(f"{self._api}/getFile",
                                params={"file_id": voice["file_id"]}, timeout=20).json()
            path = info["result"]["file_path"]
            ogg  = requests.get(
                f"https://api.telegram.org/file/bot{self._token}/{path}", timeout=30
            ).content
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
                f.write(ogg)
                ogg_path = f.name
            wav_path = ogg_path + ".wav"
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", ogg_path, "-ar", "16000", "-ac", "1",
                 "-f", "wav", wav_path],
                capture_output=True, timeout=30,
            )
            if r.returncode != 0:
                raise RuntimeError(r.stderr.decode(errors="replace")[-200:])
            import numpy as np
            import wave
            with wave.open(wav_path, "rb") as w:
                pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            audio = pcm.astype(np.float32) / 32768.0
            for p in (ogg_path, wav_path):
                Path(p).unlink(missing_ok=True)
            return (self._j._stt.transcribe(audio) or "").strip()
        except Exception as e:
            log.error("voice note: %s", e)
            self._send_text(chat_id, f"Não consegui transcrever o áudio: {e}")
            return ""

    # ── voz: saída (EdgeTTS MP3, independente do engine local) ──────────
    def _send_tts_audio(self, chat_id: int, text: str) -> None:
        try:
            import asyncio
            import edge_tts

            async def _synth() -> bytes:
                voice = self._j._config.get("telegram_tts_voice", "pt-BR-AntonioNeural")
                comm = edge_tts.Communicate(text[:800], voice)
                buf = bytearray()
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        buf.extend(chunk["data"])
                return bytes(buf)

            loop = asyncio.new_event_loop()
            try:
                mp3 = loop.run_until_complete(_synth())
            finally:
                loop.close()
            if not mp3:
                return
            requests.post(
                f"{self._api}/sendAudio",
                data={"chat_id": chat_id, "title": "Jarvis"},
                files={"audio": ("jarvis.mp3", mp3, "audio/mpeg")},
                timeout=60,
            )
        except Exception as e:
            log.warning("áudio de resposta falhou: %s", e)   # texto já foi enviado

    # ── envio ────────────────────────────────────────────────────────────
    def _send_text(self, chat_id: int, text: str) -> None:
        try:
            for i in range(0, len(text), 4000):        # limite do Telegram: 4096
                requests.post(f"{self._api}/sendMessage",
                              json={"chat_id": chat_id, "text": text[i:i + 4000]},
                              timeout=20)
        except Exception as e:
            log.error("sendMessage: %s", e)
