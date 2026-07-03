"""
MARK XL — Email tool (IMAP read / SMTP send).

Requires config in api_keys.json:
    "email_imap_host": "imap.gmail.com",
    "email_imap_port": 993,
    "email_smtp_host": "smtp.gmail.com",
    "email_smtp_port": 587,
    "email_address": "you@gmail.com",
    "email_password": "app-password-here",
"""
from __future__ import annotations

import imaplib
import json
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
from pathlib import Path

from core.paths import API_CONFIG_PATH
from core.logger import get_logger

log = get_logger("email_tool")


def _load_config() -> dict:
    try:
        return json.loads(API_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _send_email(params: dict) -> str:
    cfg = _load_config()
    smtp_host = params.get("smtp_host") or cfg.get("email_smtp_host", "smtp.gmail.com")
    smtp_port = int(params.get("smtp_port") or cfg.get("email_smtp_port", 587))
    sender    = params.get("from") or cfg.get("email_address", "")
    password  = params.get("password") or cfg.get("email_password", "")
    to        = params.get("to", "")
    subject   = params.get("subject", "No Subject")
    body      = params.get("body", "")

    if not sender or not password:
        return "Email not configured. Set email_address and email_password in config."
    if not to:
        return "No recipient specified."

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.starttls(context=context)
            server.login(sender, password)
            server.send_message(msg)
        log.info("Email sent to %s", to)
        return f"Email sent to {to}."
    except Exception as e:
        log.error("Email send failed: %s", e)
        return f"Failed to send email: {e}"


def _decode_hdr(raw: str | None) -> str:
    """Decode a possibly RFC2047-encoded header (all parts, not just the first)."""
    if not raw:
        return ""
    parts = []
    for value, enc in decode_header(raw):
        if isinstance(value, bytes):
            value = value.decode(enc or "utf-8", errors="replace")
        parts.append(value)
    return "".join(parts).strip()


def _friendly_sender(from_hdr: str) -> str:
    """'\"João Silva\" <joao@x.com>' → 'João Silva'; bare address keeps user part."""
    name = _decode_hdr(from_hdr)
    if "<" in name:
        display = name.split("<")[0].strip().strip('"')
        if display:
            return display
        name = name.split("<")[1].rstrip(">")
    return name.split("@")[0] if "@" in name else name


def _connect_imap(params: dict, cfg: dict):
    imap_host = params.get("imap_host") or cfg.get("email_imap_host", "imap.gmail.com")
    imap_port = int(params.get("imap_port") or cfg.get("email_imap_port", 993))
    user      = params.get("from") or cfg.get("email_address", "")
    password  = params.get("password") or cfg.get("email_password", "")
    if not user or not password:
        return None
    mail = imaplib.IMAP4_SSL(imap_host, imap_port)
    mail.login(user, password)
    return mail


def _fetch_summaries(mail, msg_ids: list, limit: int) -> list[str]:
    """Voice-friendly one-liners, newest first.

    Uses BODY.PEEK — a plain RFC822 fetch silently marks every listed email
    as read, which surprises users checking mail by voice.
    """
    import email as _email
    lines = []
    for i, mid in enumerate(reversed(msg_ids[-limit:]), 1):
        _, msg_data = mail.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
        msg     = _email.message_from_bytes(msg_data[0][1])
        sender  = _friendly_sender(msg.get("From", ""))
        subject = _decode_hdr(msg.get("Subject")) or "sem assunto"
        lines.append(f"{i}: de {sender}, assunto: {subject}.")
    return lines


def _read_email(params: dict) -> str:
    cfg    = _load_config()
    folder = params.get("folder", "INBOX")
    limit  = max(1, min(int(params.get("limit", 5)), 10))
    unread_only = params.get("unread_only", True)
    if isinstance(unread_only, str):
        unread_only = unread_only.strip().lower() not in ("false", "0", "no", "nao", "não")

    if _gmail_ready():
        try:
            q = "is:unread in:inbox" if unread_only else "in:inbox"
            total, top, _ = _gmail_query(q, limit)
            if total == 0 and unread_only:
                return "Nenhum e-mail não lido, senhor."
            if not top:
                return "Nenhum e-mail encontrado."
            lines = [f"{i}: de {s}, assunto: {a}." for i, (s, a) in enumerate(top, 1)]
            label = "não lidos" if unread_only else "recentes"
            head  = (f"{total} e-mails {label}." if total > 1
                     else f"1 e-mail {'não lido' if unread_only else 'recente'}.")
            if total > len(lines):
                head += f" Os {len(lines)} mais recentes:"
            return head + " " + " ".join(lines)
        except Exception as e:
            log.warning("Gmail API read falhou (%s) — tentando IMAP", e)

    try:
        mail = _connect_imap(params, cfg)
        if mail is None:
            return "Email não configurado. Defina email_address e email_password no config."
        mail.select(folder, readonly=True)

        criteria = "UNSEEN" if unread_only else "ALL"
        _, data = mail.search(None, criteria)
        msg_ids = data[0].split()

        if not msg_ids and unread_only:
            mail.logout()
            return "Nenhum e-mail não lido, senhor."
        if not msg_ids:
            mail.logout()
            return "Nenhum e-mail encontrado."

        lines = _fetch_summaries(mail, msg_ids, limit)
        mail.logout()

        total = len(msg_ids)
        label = "não lidos" if unread_only else "recentes"
        head  = f"{total} e-mails {label}." if total > 1 else f"1 e-mail {'não lido' if unread_only else 'recente'}."
        if total > limit:
            head += f" Os {len(lines)} mais recentes:"
        return head + " " + " ".join(lines)

    except Exception as e:
        log.error("Email read failed: %s", e)
        return f"Falha ao ler e-mails: {e}"


# ---------------------------------------------------------------------------
# Gmail API backend (preferred when OAuth is configured; IMAP is the fallback)
# ---------------------------------------------------------------------------

def _gmail_ready() -> bool:
    try:
        from core.google_auth import google_ready
        return google_ready()
    except Exception:
        return False


def _gmail_service():
    from googleapiclient.discovery import build
    from core.google_auth import get_credentials
    return build("gmail", "v1", credentials=get_credentials(), cache_discovery=False)


def _gmail_headers(svc, msg_id: str) -> tuple[str, str]:
    msg = svc.users().messages().get(
        userId="me", id=msg_id, format="metadata",
        metadataHeaders=["From", "Subject"],
    ).execute()
    hdrs = {h["name"].lower(): h["value"]
            for h in msg.get("payload", {}).get("headers", [])}
    return _friendly_sender(hdrs.get("from", "")), (hdrs.get("subject") or "sem assunto")


def _gmail_query(query: str, limit: int) -> tuple[int, list[tuple[str, str]], list[str]]:
    """Run a Gmail search. Returns (approx_total, [(sender, subject)...], ids)."""
    svc  = _gmail_service()
    resp = svc.users().messages().list(
        userId="me", q=query, maxResults=limit,
    ).execute()
    msgs  = resp.get("messages", [])
    total = resp.get("resultSizeEstimate", len(msgs))
    top   = [_gmail_headers(svc, m["id"]) for m in msgs]
    return total, top, [m["id"] for m in msgs]


def _gmail_mark_read(params: dict) -> str:
    limit = max(1, min(int(params.get("limit", 10)), 25))
    svc   = _gmail_service()
    resp  = svc.users().messages().list(
        userId="me", q="is:unread in:inbox", maxResults=limit,
    ).execute()
    ids = [m["id"] for m in resp.get("messages", [])]
    if not ids:
        return "Nenhum e-mail não lido para marcar."
    svc.users().messages().batchModify(
        userId="me", body={"ids": ids, "removeLabelIds": ["UNREAD"]},
    ).execute()
    plural = "e-mails marcados" if len(ids) > 1 else "e-mail marcado"
    return f"{len(ids)} {plural} como lido."


def unread_summary(limit: int = 3) -> tuple[int, list[tuple[str, str]]]:
    """Structured unread overview for the proactive engine.

    Returns (total_unread, [(sender, subject), ...] newest first).
    (0, []) when unconfigured or on any error — the engine must never crash
    or nag because IMAP hiccuped.
    """
    if _gmail_ready():
        try:
            total, top, _ = _gmail_query("is:unread in:inbox", limit)
            return total, top
        except Exception as e:
            log.warning("Gmail API unread_summary falhou (%s) — tentando IMAP", e)

    import email as _email
    cfg = _load_config()
    try:
        mail = _connect_imap({}, cfg)
        if mail is None:
            return 0, []
        mail.select("INBOX", readonly=True)
        _, data = mail.search(None, "UNSEEN")
        msg_ids = data[0].split()
        top: list[tuple[str, str]] = []
        for mid in reversed(msg_ids[-limit:]):
            _, msg_data = mail.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            msg = _email.message_from_bytes(msg_data[0][1])
            top.append((
                _friendly_sender(msg.get("From", "")),
                _decode_hdr(msg.get("Subject")) or "sem assunto",
            ))
        mail.logout()
        return len(msg_ids), top
    except Exception as e:
        log.warning("unread_summary failed: %s", e)
        return 0, []


def _search_email(params: dict) -> str:
    """IMAP search by sender / subject / recency — result is voice-friendly."""
    cfg        = _load_config()
    folder     = params.get("folder", "INBOX")
    limit      = max(1, min(int(params.get("limit", 5)), 10))
    sender     = (params.get("sender") or "").strip()
    subject    = (params.get("subject") or "").strip()
    since_days = int(params.get("since_days", 0) or 0)

    if not sender and not subject and not since_days:
        return "Diga o remetente, o assunto ou o período que devo buscar."

    if _gmail_ready():
        try:
            q_parts = []
            if sender:
                q_parts.append(f"from:({sender})")
            if subject:
                q_parts.append(f"subject:({subject})")
            if since_days:
                q_parts.append(f"newer_than:{since_days}d")
            total, top, _ = _gmail_query(" ".join(q_parts), limit)
            if not top:
                return "Nenhum e-mail encontrado com esses critérios."
            lines = [f"{i}: de {s}, assunto: {a}." for i, (s, a) in enumerate(top, 1)]
            head  = f"Encontrei {total} e-mail{'s' if total > 1 else ''}."
            if total > len(lines):
                head += f" Os {len(lines)} mais recentes:"
            return head + " " + " ".join(lines)
        except Exception as e:
            log.warning("Gmail API search falhou (%s) — tentando IMAP", e)

    criteria: list[str] = []
    if sender:
        criteria += ["FROM", f'"{sender}"']
    if subject:
        criteria += ["SUBJECT", f'"{subject}"']
    if since_days:
        from datetime import datetime, timedelta
        date = (datetime.now() - timedelta(days=since_days)).strftime("%d-%b-%Y")
        criteria += ["SINCE", date]

    try:
        mail = _connect_imap(params, cfg)
        if mail is None:
            return "Email não configurado. Defina email_address e email_password no config."
        mail.select(folder, readonly=True)

        try:
            # UTF-8 first — sender/subject may contain accents (pt-BR).
            _, data = mail.search("UTF-8", *criteria)
        except Exception:
            _, data = mail.search(None, *criteria)
        msg_ids = data[0].split()

        if not msg_ids:
            mail.logout()
            return "Nenhum e-mail encontrado com esses critérios."

        lines = _fetch_summaries(mail, msg_ids, limit)
        mail.logout()

        total = len(msg_ids)
        head  = f"Encontrei {total} e-mail{'s' if total > 1 else ''}."
        if total > limit:
            head += f" Os {len(lines)} mais recentes:"
        return head + " " + " ".join(lines)

    except Exception as e:
        log.error("Email search failed: %s", e)
        return f"Falha na busca de e-mails: {e}"


def email_tool(
    parameters: dict,
    player=None,
    speak=None,
) -> str:
    params = parameters or {}
    action = params.get("action", "send").lower()

    if player:
        player.write_log(f"SYS: Email — {action}")

    if action == "send":
        return _send_email(params)
    elif action == "read":
        return _read_email(params)
    elif action == "search":
        return _search_email(params)
    elif action == "mark_read":
        if not _gmail_ready():
            from core.google_auth import SETUP_HINT
            return SETUP_HINT
        try:
            return _gmail_mark_read(params)
        except Exception as e:
            return f"Falha ao marcar como lido: {e}"
    else:
        return f"Unknown email action: {action}. Use 'send', 'read', 'search' or 'mark_read'."
