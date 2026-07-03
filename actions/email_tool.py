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
    else:
        return f"Unknown email action: {action}. Use 'send', 'read' or 'search'."
