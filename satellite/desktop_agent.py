"""
JARVIS — Satélite de Desktop (Fase 2).

Serviço leve que roda NA MÁQUINA do usuário (sessão gráfica) e expõe, só
para a tailnet, as capacidades que o cérebro no VPS não tem:

  POST /open_app     {app_name}            → abre app/site no desktop
  POST /screen_look  {question}            → screenshot + visão Groq
                                             llama-4-scout (~4s); local
                                             Ollama gemma4 é fallback, ou
                                             primário c/ vision_prefer_local
  GET  /health                             → estado

Segurança:
  - autenticação por token (config: satellite_token) no header X-Jarvis-Token
  - bind no IP da tailnet (config: satellite_bind) — inalcançável fora dela
  - screenshot NUNCA é armazenado: vive em /dev/shm e é apagado no finally
  - modo privado: config satellite_screen_disabled=true desliga a visão

Instalação (uma vez):
    systemctl --user enable --now $(pwd)/satellite/jarvis-satellite.service
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from fastapi import FastAPI, Header, HTTPException, Request
import uvicorn

CFG_PATH = BASE_DIR / "config" / "api_keys.json"


def _cfg() -> dict:
    try:
        return json.loads(CFG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


app = FastAPI(docs_url=None, redoc_url=None)


def _auth(token: str | None) -> None:
    expected = _cfg().get("satellite_token", "")
    if not expected or token != expected:
        raise HTTPException(403, "token inválido")


@app.get("/health")
async def health():
    return {"ok": True, "display": bool(os.environ.get("DISPLAY")
                                        or os.environ.get("WAYLAND_DISPLAY"))}


@app.post("/open_app")
async def open_app_ep(req: Request, x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    body = await req.json()
    name = (body.get("app_name") or "").strip()
    if not name:
        return {"result": "Nome do aplicativo vazio."}
    from actions.open_app import open_app
    r = open_app(parameters={"app_name": name}, response=None, player=None)
    return {"result": r or f"Abri {name}."}


# ── captura de tela (X11 e Wayland/GNOME) ────────────────────────────────

def _screenshot(path: str) -> str | None:
    """Tenta capturar a tela; retorna None se ok, ou a mensagem de erro."""
    # Serviço --user pode nascer sem o ambiente gráfico — garante os
    # mínimos para gnome-screenshot/portal funcionarem.
    os.environ.setdefault("DISPLAY", ":0")
    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS",
                          f"unix:path=/run/user/{os.getuid()}/bus")
    attempts = [
        # GNOME/Wayland: portal com permissão pré-concedida (ver portal_shot.py);
        # gi só existe no Python do sistema, por isso o subprocesso.
        ["/usr/bin/python3", str(Path(__file__).parent / "portal_shot.py"), path],
        ["gnome-screenshot", "-f", path],
        ["gdbus", "call", "--session", "--dest", "org.gnome.Shell",
         "--object-path", "/org/gnome/Shell/Screenshot",
         "--method", "org.gnome.Shell.Screenshot.Screenshot",
         "true", "false", path],
        ["grim", path],
        ["import", "-window", "root", path],   # X11 (ImageMagick)
    ]
    errors = []
    for cmd in attempts:
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=15)
            if r.returncode == 0 and Path(path).exists() \
                    and Path(path).stat().st_size > 1000:
                return None
            errors.append(f"{cmd[0]}: rc={r.returncode}")
        except FileNotFoundError:
            errors.append(f"{cmd[0]}: ausente")
        except Exception as e:
            errors.append(f"{cmd[0]}: {e}")
    return "; ".join(errors)


def _vision_local(image_b64: str, question: str) -> str | None:
    """Ollama local (gemma4 multimodal) — visão sem sair da máquina.

    Medido em 2026-07-08: ~150s por resposta em CPU mesmo com o modelo
    quente — inviável em conversa. Por isso é fallback; vira primário só
    com vision_prefer_local=true (privacidade acima da velocidade).
    """
    try:
        import requests
        model = _cfg().get("vision_local_model", "gemma4")
        r = requests.post("http://localhost:11434/api/generate", json={
            "model": model, "stream": False,
            "prompt": ("Você vê a tela do computador do usuário. Responda em "
                       "português brasileiro, direto e útil (3-6 frases). "
                       f"Pergunta: {question}"),
            "images": [image_b64],
        }, timeout=240)
        if r.status_code == 200:
            return (r.json().get("response") or "").strip() or None
    except Exception:
        pass
    return None


def _vision_groq(image_b64: str, question: str) -> str | None:
    """Fallback nuvem: Groq llama-4-scout (multimodal)."""
    try:
        import requests
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {_cfg()['groq_api_key']}"},
            json={"model": "meta-llama/llama-4-scout-17b-16e-instruct",
                  "max_tokens": 500,
                  "messages": [{"role": "user", "content": [
                      {"type": "text", "text":
                          "Tela do computador do usuário. Responda em pt-BR, "
                          f"direto (3-6 frases). Pergunta: {question}"},
                      {"type": "image_url", "image_url": {
                          "url": f"data:image/png;base64,{image_b64}"}},
                  ]}]},
            timeout=60,
        ).json()
        return (r["choices"][0]["message"]["content"] or "").strip() or None
    except Exception:
        return None


@app.post("/screen_look")
async def screen_look_ep(req: Request, x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    if _cfg().get("satellite_screen_disabled", False):
        return {"result": "Modo privado ativo: captura de tela desativada "
                          "(satellite_screen_disabled)."}
    body = await req.json()
    question = (body.get("question") or "O que está na tela?").strip()

    # /dev/shm = RAM: o screenshot nunca toca o disco e é apagado sempre
    fd, path = tempfile.mkstemp(suffix=".png", dir="/dev/shm")
    os.close(fd)
    try:
        err = _screenshot(path)
        if err:
            return {"result": f"Não consegui capturar a tela ({err})."}
        b64 = base64.b64encode(Path(path).read_bytes()).decode("ascii")
        engines = [(_vision_groq, "visão em nuvem (Groq)"),
                   (_vision_local, "visão local (Ollama)")]
        if _cfg().get("vision_prefer_local", False):
            engines.reverse()
        answer = engine = None
        for fn, name in engines:
            answer = fn(b64, question)
            if answer:
                engine = name
                break
        if not answer:
            return {"result": "Capturei a tela, mas nenhum motor de visão "
                              "respondeu (Groq e Ollama local falharam)."}
        return {"result": answer, "engine": engine}
    finally:
        Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    bind = _cfg().get("satellite_bind", "0.0.0.0")
    port = int(_cfg().get("satellite_port", 7861))
    uvicorn.run(app, host=bind, port=port, log_level="warning")
