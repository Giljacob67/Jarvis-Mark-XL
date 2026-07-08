#!/usr/bin/env bash
# JARVIS Mark XL — abre o cliente de voz v2 (Pipecat no VPS, via tailnet).
#
# O app Qt da Geração 1 foi APOSENTADO em 2026-07-08: a voz agora é o
# cliente web (WebRTC + AEC nativo do navegador) falando com o servidor
# 24/7. Para arqueologia, o launcher antigo era: .venv/bin/python main.py
set -euo pipefail

URL="https://ubuntu-8gb-hel1-1.tail54aaa6.ts.net/voz"

# --app = janela própria, sem barra de abas (parece app nativo)
for browser in google-chrome google-chrome-stable /snap/bin/chromium \
               /snap/bin/brave; do
  if command -v "$browser" >/dev/null 2>&1; then
    exec "$browser" --app="$URL"
  fi
done
exec firefox --new-window "$URL"   # firefox não tem modo --app
