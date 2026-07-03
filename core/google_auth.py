"""
MARK XL — Shared Google OAuth for Gmail API + Google Calendar API.

Files (both gitignored — never committed):
    config/google_credentials.json  — OAuth client (Desktop app) downloaded
                                      from Google Cloud Console
    config/google_token.json        — user token, created by the one-time
                                      consent flow (scripts/setup_google.py)

Runtime tools call get_credentials() (non-interactive: refreshes silently,
raises if consent is missing).  The browser consent flow only ever runs from
scripts/setup_google.py — never mid-conversation.
"""
from __future__ import annotations

from pathlib import Path

from core.paths import CONFIG_DIR
from core.logger import get_logger

log = get_logger("google_auth")

CREDENTIALS_PATH = CONFIG_DIR / "google_credentials.json"
TOKEN_PATH       = CONFIG_DIR / "google_token.json"

# gmail.modify = read + labels (mark as read); calendar.events = read/write events
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
]

SETUP_HINT = (
    "Integração Google não configurada. Rode no terminal: "
    ".venv/bin/python scripts/setup_google.py"
)


def google_ready() -> bool:
    """Cheap check used by tools to decide API vs fallback (IMAP/local)."""
    return TOKEN_PATH.exists() and CREDENTIALS_PATH.exists()


def get_credentials(interactive: bool = False):
    """Return valid google.oauth2 Credentials.

    Refreshes expired tokens silently.  When consent is missing:
    interactive=True runs the browser flow (setup script only);
    interactive=False raises RuntimeError with setup instructions.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = None
    if TOKEN_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        except Exception as e:
            log.warning("Token inválido (%s) — novo consentimento necessário", e)
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds)
            return creds
        except Exception as e:
            log.warning("Refresh falhou (%s) — novo consentimento necessário", e)
            creds = None

    if not interactive:
        raise RuntimeError(SETUP_HINT)

    if not CREDENTIALS_PATH.exists():
        raise RuntimeError(
            f"Arquivo {CREDENTIALS_PATH} não encontrado. Baixe o OAuth client "
            "(tipo 'Desktop app') no Google Cloud Console e salve nesse caminho."
        )

    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    _save_token(creds)
    return creds


def _save_token(creds) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    log.info("Token Google salvo em %s", TOKEN_PATH)
