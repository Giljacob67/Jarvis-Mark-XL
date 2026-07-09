"""
JARVIS v2 — serviços 24/7 (rodam com o SERVIDOR, não com a sessão de voz).

  TelegramService   texto e voice notes de qualquer lugar → text_agent
                    (mesmo cérebro/ferramentas/permissões da voz)
  ProactiveService  briefing matinal + checagens de e-mail + lembretes de
                    compromisso — falados na sessão de voz ativa (se houver)
                    E enviados por Telegram

Ativação: SOMENTE com JARVIS_SERVICES=1 no ambiente (a unit do VPS define;
o POC local de voz não — evita pollers/briefings duplicados).

Reuso deliberado: helpers puros e testados de core/proactive.py
(in_quiet_hours, time_slot_due) e o mesmo state file — dedupe preservado.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from loguru import logger

CFG_PATH = BASE_DIR / "config" / "api_keys.json"


def _cfg() -> dict:
    try:
        return json.loads(CFG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ── saída unificada: voz ativa (se houver) + Telegram ────────────────────

def _say_voice(text: str) -> bool:
    """Fala na sessão de voz ativa. False se ninguém conectado."""
    try:
        from poc import tools_bridge
        if tools_bridge._say_hook:
            tools_bridge._say(text)
            return True
    except Exception:
        pass
    return False


class TelegramService:
    def __init__(self):
        cfg = _cfg()
        self._token = (cfg.get("telegram_bot_token") or "").strip()
        self._api = f"https://api.telegram.org/bot{self._token}"
        self._offset = 0
        self._history: list[dict] = []   # memória curta do canal texto

    def start(self):
        if not self._token:
            logger.warning("Telegram sem token — serviço desativado")
            return
        threading.Thread(target=self._loop, daemon=True, name="tg-v2").start()
        logger.info("TelegramService v2 ativo")

    def _owner(self) -> int | None:
        v = _cfg().get("telegram_owner_id")
        return int(v) if v else None

    # envio usado também pela proatividade
    def send(self, text: str, audio: bool = False) -> None:
        import requests
        owner = self._owner()
        if not owner or not self._token:
            return
        try:
            for i in range(0, len(text), 4000):
                requests.post(f"{self._api}/sendMessage",
                              json={"chat_id": owner, "text": text[i:i + 4000]},
                              timeout=20)
            if audio and _cfg().get("telegram_voice_replies", True):
                self._send_tts(owner, text)
        except Exception as e:
            logger.error(f"telegram send: {e}")

    def _send_tts(self, chat_id: int, text: str) -> None:
        try:
            import asyncio

            import edge_tts
            import requests

            async def _synth() -> bytes:
                voice = _cfg().get("telegram_tts_voice", "pt-BR-AntonioNeural")
                # 4000 ≈ 4-5 min de fala — o podcast matinal cabe inteiro
                # (o teto antigo de 800 decapitava qualquer narração)
                comm = edge_tts.Communicate(text[:4000], voice)
                buf = bytearray()
                async for ch in comm.stream():
                    if ch["type"] == "audio":
                        buf.extend(ch["data"])
                return bytes(buf)

            loop = asyncio.new_event_loop()
            try:
                mp3 = loop.run_until_complete(_synth())
            finally:
                loop.close()
            if mp3:
                requests.post(f"{self._api}/sendAudio",
                              data={"chat_id": chat_id, "title": "Jarvis"},
                              files={"audio": ("jarvis.mp3", mp3, "audio/mpeg")},
                              timeout=60)
        except Exception as e:
            logger.warning(f"áudio telegram falhou: {e}")

    # ── entrada ──────────────────────────────────────────────────────────
    def _loop(self):
        import requests

        from core.health import beat, fail
        while True:
            try:
                beat("telegram")
                resp = requests.get(f"{self._api}/getUpdates",
                                    params={"offset": self._offset, "timeout": 50},
                                    timeout=60).json()
                for upd in resp.get("result", []):
                    self._offset = upd["update_id"] + 1
                    try:
                        self._handle(upd)
                    except Exception as e:
                        logger.error(f"tg update: {e}")
            except requests.exceptions.RequestException:
                time.sleep(5)
            except Exception as e:
                logger.error(f"tg poll: {e}")
                fail("telegram", str(e))
                time.sleep(5)

    def _handle(self, upd: dict):
        import requests
        msg = upd.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        user_id = (msg.get("from") or {}).get("id")
        owner = self._owner()
        if not chat_id or user_id != owner:
            return

        text = (msg.get("text") or "").strip()
        if msg.get("voice"):
            text = self._transcribe(msg["voice"])
            if not text:
                self.send("Não consegui transcrever o áudio.")
                return
            requests.post(f"{self._api}/sendMessage",
                          json={"chat_id": chat_id, "text": f"🎤 «{text}»"},
                          timeout=15)
        if not text or text.startswith("/start"):
            return

        logger.info(f"[Telegram] {text[:80]}")
        from poc.text_agent import run_turn
        reply = run_turn(text, history=self._history)
        self._history += [{"role": "user", "content": text},
                          {"role": "assistant", "content": reply}]
        self._history = self._history[-16:]
        self.send(reply, audio=True)

    def _transcribe(self, voice: dict) -> str:
        """Voice note OGG/Opus → Deepgram prerecorded (aceita ogg nativo)."""
        import requests
        try:
            info = requests.get(f"{self._api}/getFile",
                                params={"file_id": voice["file_id"]},
                                timeout=20).json()
            ogg = requests.get(
                f"https://api.telegram.org/file/bot{self._token}/"
                f"{info['result']['file_path']}", timeout=30).content
            r = requests.post(
                "https://api.deepgram.com/v1/listen",
                params={"model": _cfg().get("deepgram_model", "nova-2"),
                        "language": "pt-BR", "smart_format": "true"},
                headers={"Authorization": f"Token {_cfg()['deepgram_api_key']}",
                         "Content-Type": "audio/ogg"},
                data=ogg, timeout=30,
            ).json()
            return (r["results"]["channels"][0]["alternatives"][0]
                    ["transcript"]).strip()
        except Exception as e:
            logger.error(f"tg voice STT: {e}")
            return ""


class ProactiveService:
    """Briefing matinal + e-mails + lembretes — reusa helpers/estado da G1."""

    _STATE = BASE_DIR / "memory" / "proactive_state.json"

    def __init__(self, telegram: TelegramService):
        self._tg = telegram
        self._state = self._load()

    def _load(self) -> dict:
        try:
            return json.loads(self._STATE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self):
        try:
            self._STATE.write_text(
                json.dumps(self._state, indent=2, ensure_ascii=False),
                encoding="utf-8")
        except Exception as e:
            logger.warning(f"estado proativo: {e}")

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="proactive-v2").start()
        logger.info("ProactiveService v2 ativo")

    def _announce(self, text: str) -> None:
        spoke = _say_voice(text)
        self._tg.send(text, audio=not spoke)   # voz local OU áudio no Telegram
        try:
            from core.presence import PresenceState, get_presence
            get_presence().transition(PresenceState.PROACTIVE_WAITING,
                                      "anúncio enviado")
        except Exception:
            pass

    def _loop(self):
        from datetime import datetime

        from core.health import beat, fail
        from core.proactive import in_quiet_hours, time_slot_due
        time.sleep(20)
        while True:
            try:
                cfg = _cfg()
                beat("proactive")   # antes dos gates: quieto ≠ morto
                if not cfg.get("proactive_enabled", True):
                    time.sleep(60); continue
                now = datetime.now()
                if in_quiet_hours(now, cfg.get("proactive_quiet_hours",
                                               "22:30-07:00")):
                    time.sleep(60); continue

                # briefing matinal — podcast narrado (proactive_briefing_mode
                # volta para 'completo' se o usuário preferir o telegráfico)
                slot = str(cfg.get("proactive_morning_briefing", "08:30"))
                if time_slot_due(now, slot, self._state.get("briefing_date"),
                                 grace_min=180):
                    self._state["briefing_date"] = now.strftime("%Y-%m-%d")
                    self._save()
                    from poc.briefing import generate
                    self._announce(generate(
                        cfg.get("proactive_briefing_mode", "podcast")))

                # checagens de e-mail — boletim NARRADO (só fala se houver
                # não lidos; conteúdo via snippets, não só assuntos)
                done = self._state.setdefault("email_checks", {})
                for s in cfg.get("proactive_email_checks",
                                 ["12:00", "15:30", "18:00"]):
                    if time_slot_due(now, s, done.get(s)):
                        done[s] = now.strftime("%Y-%m-%d")
                        self._save()
                        from poc.briefing import email_bulletin
                        text = email_bulletin()
                        if text:
                            self._announce(text)
                        break

                # radar de prazos: varre o Gmail a cada radar_interval_min
                # e anuncia NA HORA qualquer intimação nova com prazo
                if cfg.get("radar_enabled", True):
                    interval = int(cfg.get("radar_interval_min", 60))
                    last = self._state.get("radar_last_scan", 0)
                    if time.time() - last >= interval * 60:
                        self._state["radar_last_scan"] = time.time()
                        self._save()
                        from poc.radar import scan
                        beat("radar")
                        for p in scan():
                            self._announce(
                                f"Senhor, intimação nova no radar: {p['resumo']} "
                                f"Prazo estimado: {p['data_limite']} — evento "
                                f"criado na agenda; confirme no sistema.")

                # lembretes de compromisso (60/15 min, dedupe persistente)
                from actions.calendar_tool import agenda_upcoming
                reminded = self._state.setdefault("reminded", {})
                for ev in agenda_upcoming(hours=26):
                    if ev.get("all_day"):
                        continue
                    mins = (ev["start"] - now).total_seconds() / 60
                    for th in sorted(cfg.get("proactive_event_reminders_min",
                                             [60, 15])):
                        if 0 < mins <= th:
                            key = f"{ev['start']:%Y-%m-%d %H:%M}|{ev['title']}|{th}"
                            if key in reminded:
                                break
                            reminded[key] = now.strftime("%Y-%m-%d")
                            self._save()
                            self._announce(
                                f"Senhor, lembrete: {ev['title']} em "
                                f"{int(mins)} minutos, às {ev['start']:%H:%M}.")
                            break
            except Exception as e:
                logger.error(f"proactive tick: {e}")
                fail("proactive", str(e))
            time.sleep(30)


# ── bootstrap único do processo ──────────────────────────────────────────
_started = False
_lock = threading.Lock()


def ensure_services_started() -> bool:
    """Inicia Telegram + Proatividade UMA vez. Gate: JARVIS_SERVICES=1."""
    global _started
    import os
    if os.environ.get("JARVIS_SERVICES") != "1":
        return False
    with _lock:
        if _started:
            return True
        _started = True
    tg = TelegramService()
    tg.start()
    ProactiveService(tg).start()
    logger.info("Serviços 24/7 iniciados (Telegram + Proatividade)")
    return True
