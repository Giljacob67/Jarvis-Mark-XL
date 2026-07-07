"""Gera poc/qr.png com a URL do cliente de voz (para escanear no iPhone/Mac).

O QR é só conveniência de digitação — a segurança é a tailnet (a URL não
resolve fora dos seus aparelhos Tailscale). Rode após mudar o hostname:

    .venv-pipecat/bin/python poc/make_qr.py
"""
from __future__ import annotations

from pathlib import Path

URL = "https://ubuntu-8gb-hel1-1.tail54aaa6.ts.net/files/client.html"


def main() -> None:
    import qrcode
    img = qrcode.make(URL)
    out = Path(__file__).parent / "qr.png"
    img.save(out)
    print(f"QR gerado: {out}  →  {URL}")
    print("Acesse /files/qr.png no navegador do desktop e escaneie com o iPhone.")


if __name__ == "__main__":
    main()
