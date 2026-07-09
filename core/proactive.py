"""
MARK XL — Proactive engine: scheduled checks that produce spontaneous speech.

Checks (all opt-in via config, see readme):
  * morning briefing  — once a day at a set time: today's agenda + unread mail
  * email checks      — at key times, speak ONLY when there are unread emails
  * event reminders   — agenda events approaching (default: 60 and 15 min out)

Design rules:
  - Never interrupt: if the assistant is speaking or a turn is running,
    the check is retried on the next tick, not dropped.
  - Quiet hours and mic-mute suppress all proactive speech.
  - State (what was already announced) persists across restarts in
    memory/proactive_state.json so nothing is repeated after a reboot.
  - Data gathering is deterministic (direct tool calls); only the phrasing
    of the morning briefing goes through the LLM, with a template fallback.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from core.logger import get_logger

log = get_logger("proactive")

_STATE_PATH = Path(__file__).resolve().parent.parent / "memory" / "proactive_state.json"

_TICK_SECONDS = 30


def _parse_hhmm(s: str) -> tuple[int, int] | None:
    try:
        h, m = s.strip().split(":")
        return int(h), int(m)
    except Exception:
        return None


def in_quiet_hours(now: datetime, quiet: str) -> bool:
    """quiet = 'HH:MM-HH:MM'; supports ranges crossing midnight ('22:30-07:30')."""
    try:
        start_s, end_s = quiet.split("-")
        sh, sm = _parse_hhmm(start_s)          # type: ignore[misc]
        eh, em = _parse_hhmm(end_s)            # type: ignore[misc]
    except Exception:
        return False
    cur   = now.hour * 60 + now.minute
    start = sh * 60 + sm
    end   = eh * 60 + em
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end           # crosses midnight


def time_slot_due(now: datetime, slot: str, last_done_date: str | None,
                  grace_min: int = 59) -> bool:
    """True when `slot` ('HH:MM') has passed today, wasn't done today yet,
    and we're within the grace window (avoids announcing a 08:30 briefing
    at 21:00 after the machine spent the day asleep)."""
    hm = _parse_hhmm(slot)
    if hm is None:
        return False
    if last_done_date == now.strftime("%Y-%m-%d"):
        return False
    target = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
    return target <= now <= target + timedelta(minutes=grace_min)


class ProactiveEngine:
    """Background scheduler. `jarvis` is the JarvisLocal instance."""

    def __init__(self, jarvis):
        self._j = jarvis
        self._state = self._load_state()
        self._thread: threading.Thread | None = None

    # ── state ────────────────────────────────────────────────────────────
    def _load_state(self) -> dict:
        try:
            return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_state(self) -> None:
        try:
            _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _STATE_PATH.write_text(
                json.dumps(self._state, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            log.warning("state save failed: %s", e)

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="proactive"
        )
        self._thread.start()
        log.info("Proactive engine started")

    def _cfg(self, key: str, default):
        return self._j._config.get(key, default)

    def _enabled(self) -> bool:
        return bool(self._cfg("proactive_enabled", False))

    def _can_speak_now(self) -> bool:
        """Suppressed by quiet hours / mute; deferred while a turn is live."""
        if in_quiet_hours(datetime.now(), self._cfg("proactive_quiet_hours", "22:30-07:30")):
            return False
        try:
            if self._j.ui.muted:
                return False
        except Exception:
            pass
        with self._j._speaking_lock:
            if self._j._speaking:
                return False
        if self._j._turn_lock.locked():
            return False
        return True

    def _say(self, text: str) -> None:
        self._j.ui.write_log(f"SYS: 🔔 {text}")
        self._j._dashboard_broadcast({"type": "log", "speaker": "jarvis", "text": text})
        self._j.speak(text)

    # ── main loop ────────────────────────────────────────────────────────
    def _loop(self) -> None:
        # Let startup speech ("Jarvis fully online") settle first.
        time.sleep(20)
        while True:
            try:
                if self._enabled() and self._can_speak_now():
                    self._check_event_reminders()
                    self._check_morning_briefing()
                    self._check_emails()
                    self._check_legal_radar()
            except Exception as e:
                log.error("proactive tick failed: %s", e)
            time.sleep(_TICK_SECONDS)

    # ── checks ───────────────────────────────────────────────────────────
    def _check_morning_briefing(self) -> None:
        slot = str(self._cfg("proactive_morning_briefing", "08:30"))
        now  = datetime.now()
        if not time_slot_due(now, slot, self._state.get("briefing_date"), grace_min=180):
            return

        from actions.calendar_tool import agenda_upcoming
        from actions.email_tool import unread_summary

        today = [e for e in agenda_upcoming(hours=24)
                 if e["start"].date() == now.date()]
        agenda_str = (
            "; ".join(
                f"{e['title']} (o dia todo)" if e.get("all_day")
                else f"{e['title']} às {e['start'].strftime('%H:%M')}"
                for e in today
            )
            if today else "nenhum compromisso na agenda"
        )
        n_unread, top = unread_summary(limit=2)
        mail_str = (
            f"{n_unread} e-mails não lidos, o mais recente de {top[0][0]}"
            if n_unread and top else "caixa de entrada em dia"
        )

        facts = f"Agenda de hoje: {agenda_str}. E-mail: {mail_str}."
        text = self._phrase_briefing(facts) or f"Bom dia, senhor. {facts}"

        self._state["briefing_date"] = now.strftime("%Y-%m-%d")
        self._save_state()
        self._say(text)

    def _phrase_briefing(self, facts: str) -> str | None:
        """One LLM call for natural phrasing; None on any failure → template."""
        try:
            from core.llm_client import call_llm_text, get_fast_llm_model
            sys_p = (
                "Você é o Jarvis, assistente pessoal do usuário. Transforme os fatos "
                "em um briefing matinal falado, em português brasileiro, máximo 3 "
                "frases curtas, tom direto e caloroso. Não invente NADA além dos fatos."
            )
            out = call_llm_text(facts, system=sys_p, model=get_fast_llm_model(), timeout=30)
            out = (out or "").strip()
            return out if 0 < len(out) < 500 else None
        except Exception as e:
            log.warning("briefing phrasing failed: %s", e)
            return None

    def _check_legal_radar(self) -> None:
        """Opt-in scan for court/tribunal deadlines in unread e-mail.

        Runs on its own schedule (proactive_legal_radar_slots, defaulting to
        the email-check slots). For each NEW deadline it creates a local
        calendar event and announces it once. Fully fail-closed.
        """
        if not self._cfg("proactive_legal_radar_enabled", False):
            return

        slots = self._cfg("proactive_legal_radar_slots",
                          self._cfg("proactive_email_checks",
                                    ["12:00", "15:30", "18:00"]))
        if isinstance(slots, str):
            slots = [s.strip() for s in slots.split(",") if s.strip()]

        now = datetime.now()
        last = self._state.setdefault("legal_radar_last", {})
        fired = False
        for s in slots:
            if time_slot_due(now, s, last.get(s)):
                last[s] = now.strftime("%Y-%m-%d")
                fired = True
        if not fired:
            return
        self._save_state()

        from actions.legal_radar import scan_legal_deadlines
        from actions.calendar_tool import calendar_tool

        try:
            deadlines = scan_legal_deadlines(limit=10)
        except Exception as e:
            log.error("legal radar scan falhou: %s", e)
            return

        if not deadlines:
            return

        tracked = self._state.setdefault("legal_radar", {})
        announced: list[dict] = []
        for d in deadlines:
            key = f"{d['title']}|{d['deadline']}"
            if key in tracked:
                continue
            tracked[key] = now.strftime("%Y-%m-%d")
            try:
                calendar_tool(parameters={
                    "action": "create",
                    "title": f"⚖ {d['title']} (prazo)",
                    "start": d["deadline"],
                })
            except Exception as e:
                log.warning("falha ao criar evento juridico: %s", e)
            announced.append(d)

        if announced:
            # Trim entries older than 30 days so state doesn't grow forever.
            cutoff = (now - timedelta(days=30)).strftime("%Y-%m-%d")
            self._state["legal_radar"] = {k: v for k, v in tracked.items() if v >= cutoff}
            self._save_state()
            lines = "; ".join(
                f"{a['title']} até {a['deadline']}" for a in announced
            )
            self._say(
                f"Senhor, detectei {len(announced)} prazo(s) juridico(s): {lines}."
            )

    def _check_emails(self) -> None:
        slots = self._cfg("proactive_email_checks", ["12:00", "15:30", "18:00"])
        if isinstance(slots, str):
            slots = [s.strip() for s in slots.split(",") if s.strip()]
        now  = datetime.now()
        done: dict = self._state.setdefault("email_checks", {})
        for slot in slots:
            if not time_slot_due(now, slot, done.get(slot)):
                continue
            done[slot] = now.strftime("%Y-%m-%d")
            self._save_state()
            from actions.email_tool import unread_summary
            n, top = unread_summary(limit=2)
            if n <= 0:
                return                        # silence is the feature here
            if n == 1 and top:
                self._say(f"Senhor, um e-mail não lido: de {top[0][0]}, assunto {top[0][1]}.")
            elif top:
                self._say(f"Senhor, {n} e-mails não lidos. O mais recente é de "
                          f"{top[0][0]}, assunto {top[0][1]}.")
            return

    def _check_event_reminders(self) -> None:
        thresholds = self._cfg("proactive_event_reminders_min", [60, 15])
        if isinstance(thresholds, str):
            thresholds = [int(t) for t in thresholds.split(",") if t.strip()]
        from actions.calendar_tool import agenda_upcoming
        now      = datetime.now()
        reminded: dict = self._state.setdefault("reminded", {})
        changed = False
        for ev in agenda_upcoming(hours=26):
            if ev.get("all_day"):
                continue   # no clock time → time-based reminder makes no sense
            mins = (ev["start"] - now).total_seconds() / 60
            for th in sorted(thresholds):
                if 0 < mins <= th:
                    key = f"{ev['start']:%Y-%m-%d %H:%M}|{ev['title']}|{th}"
                    if key in reminded:
                        break
                    reminded[key] = now.strftime("%Y-%m-%d")
                    changed = True
                    if mins >= 50:
                        when = f"em cerca de {int(round(mins / 10) * 10)} minutos"
                    else:
                        when = f"em {int(mins)} minutos"
                    self._say(f"Senhor, lembrete: {ev['title']} {when}, "
                              f"às {ev['start']:%H:%M}.")
                    break
        if changed:
            # Trim entries older than 2 days so the file doesn't grow forever.
            cutoff = (now - timedelta(days=2)).strftime("%Y-%m-%d")
            self._state["reminded"] = {
                k: d for k, d in reminded.items() if d >= cutoff
            }
            self._save_state()
