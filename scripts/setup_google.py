#!/usr/bin/env python3
"""
Configuração única da integração Google (Gmail API + Google Calendar).

Pré-requisito — credenciais OAuth no Google Cloud Console (~10 min):
  1. https://console.cloud.google.com → criar projeto (ex.: "Jarvis")
  2. "APIs e serviços" → "Biblioteca" → ativar:
       - Gmail API
       - Google Calendar API
  3. "Tela de consentimento OAuth" → tipo Externo → preencher nome/e-mail →
     em "Público-alvo", clique em PUBLICAR O APLICATIVO (status "Em produção";
     em modo "Teste" o token expira a cada 7 dias)
  4. "Credenciais" → "Criar credenciais" → "ID do cliente OAuth" →
     tipo "App para computador" → baixar o JSON
  5. Salvar o arquivo baixado como:  config/google_credentials.json

Depois rode:  .venv/bin/python scripts/setup_google.py
O navegador abre uma vez para o consentimento; o token fica salvo localmente
(config/google_token.json — fora do git) e se renova sozinho.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.google_auth import CREDENTIALS_PATH, get_credentials


def main() -> int:
    if not CREDENTIALS_PATH.exists():
        print(__doc__)
        print(f"✗ Arquivo não encontrado: {CREDENTIALS_PATH}")
        print("  Siga os passos acima e rode novamente.")
        return 1

    print("Abrindo o navegador para o consentimento Google…")
    get_credentials(interactive=True)
    print("✓ Token salvo.\n")

    # Auto-teste: um dado real de cada API
    print("— Auto-teste —")
    try:
        from actions.email_tool import unread_summary
        n, top = unread_summary(limit=1)
        who = f" (mais recente de {top[0][0]})" if top else ""
        print(f"✓ Gmail API: {n} e-mails não lidos{who}")
    except Exception as e:
        print(f"✗ Gmail API: {e}")
        return 1
    try:
        from actions.gcal_tool import gcal_upcoming
        evs = gcal_upcoming(hours=168)
        nxt = f" — próximo: {evs[0]['title']}" if evs else ""
        print(f"✓ Google Calendar: {len(evs)} eventos nos próximos 7 dias{nxt}")
    except Exception as e:
        print(f"✗ Google Calendar: {e}")
        return 1

    print("\nIntegração Google ativa. O Jarvis passa a usá-la automaticamente")
    print("(agenda real no briefing/lembretes, busca avançada de e-mail).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
