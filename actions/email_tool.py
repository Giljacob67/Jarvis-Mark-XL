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


def _read_email(params: dict) -> str:
    cfg = _load_config()
    imap_host = params.get("imap_host") or cfg.get("email_imap_host", "imap.gmail.com")
    imap_port = int(params.get("imap_port") or cfg.get("email_imap_port", 993))
    user      = params.get("from") or cfg.get("email_address", "")
    password  = params.get("password") or cfg.get("email_password", "")
    folder    = params.get("folder", "INBOX")
    limit     = int(params.get("limit", 5))

    if not user or not password:
        return "Email not configured. Set email_address and email_password in config."

    try:
        mail = imaplib.IMAP4_SSL(imap_host, imap_port)
        mail.login(user, password)
        mail.select(folder)

        _, data = mail.search(None, "ALL")
        msg_ids = data[0].split()

        if not msg_ids:
            mail.logout()
            return "No emails found."

        recent = msg_ids[-limit:]
        results = []
        for mid in reversed(recent):
            _, msg_data = mail.fetch(mid, "(RFC822)")
            import email as _email
            msg = _email.message_from_bytes(msg_data[0][1])

            subject, encoding = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding or "utf-8", errors="replace")

            from_addr = msg.get("From", "")
            date_str = msg.get("Date", "")

            # Get body preview
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode(errors="replace")[:200]
                        break
            else:
                body = msg.get_payload(decode=True).decode(errors="replace")[:200]

            results.append(f"From: {from_addr}\nSubject: {subject}\nDate: {date_str}\nPreview: {body.strip()[:150]}\n")

        mail.logout()
        return f"Last {len(results)} emails:\n\n" + "\n---\n".join(results)

    except Exception as e:
        log.error("Email read failed: %s", e)
        return f"Failed to read emails: {e}"


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
    else:
        return f"Unknown email action: {action}. Use 'send' or 'read'."
