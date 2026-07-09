# CLAUDE.md — Jarvis Mark XL

Assistente de voz pessoal do Gilberto Jacob (advogado, Maringá/PR — perfil
completo em `memory/user_profile.md`, gitignored). Converse e commite em
**pt-BR**. Push direto autorizado. Histórico completo de decisões em
`docs/DIARIO-DO-PROJETO.md` — **leia antes de mexer em áudio/voz**.

## Duas gerações no mesmo repo

| | Geração 1 (legado) | **Geração 2 (ATUAL)** |
|---|---|---|
| Branch | `main` | `pipecat-poc` |
| Voz | app Qt + PortAudio (main.py) | **Pipecat 1.4 + WebRTC** (`poc/bot.py`) |
| Cliente | janela desktop | navegador (`poc/voz.html`) — AEC nativo + HUD |
| Status | **APOSENTADA (2026-07-08)** | **validada pelo usuário**, em produção no VPS |

A G1 não roda mais em lugar nenhum: `run.sh` (e os atalhos do desktop)
agora abrem o cliente de voz v2 no Chrome em modo --app; main.py fica no
repo só como arqueologia. Telegram e proatividade JÁ SÃO v2
(poc/services.py, no VPS). O CÉREBRO compartilhado segue vivo e em uso:
`actions/` (30+ tools), `core/tools.py` (schemas G1 referenciados pelo
registry), Gmail/Calendar OAuth (`core/google_auth.py`), memória.
A ponte da v2 é `poc/tools_bridge.py` sobre `core/registry.py`.

## Deployments

- **VPS Hetzner** (`ssh root@204.168.171.103` = `ubuntu-8gb-hel1-1` na
  tailnet `tail54aaa6.ts.net`): código em `/opt/jarvis`, serviço systemd
  `jarvis-voice` (porta 7860, só localhost). PENDENTE: usuário habilitar
  Tailscale Serve (link no diário) → URL final
  `https://ubuntu-8gb-hel1-1.tail54aaa6.ts.net`.
- **Desktop Linux** (`gilberto-jacob-ubuntu`): app Qt legado + POC local.

## Rodar a v2 localmente

```bash
python3 -m venv .venv-pipecat
.venv-pipecat/bin/pip install "pipecat-ai[silero,deepgram,openai,elevenlabs,webrtc]" \
    pipecat-ai-prebuilt fastapi "uvicorn[standard]" google-api-python-client google-auth-oauthlib
.venv-pipecat/bin/python poc/bot.py --transport webrtc --host 0.0.0.0 --port 7860 --folder poc
# cliente: http://localhost:7860/files/voz.html  (NÃO abrir via file:// sem reload)
```

## Segredos (NUNCA commitar — todos gitignored)

`config/api_keys.json` (todas as chaves: cerebras, groq, deepgram,
elevenlabs, telegram, gmail app password), `config/google_credentials.json`
+ `google_token.json` (OAuth), `config/certs/`, `memory/user_profile.md`.
Para nova máquina: copiar via scp da tailnet (desktop ou VPS `/opt/jarvis`).

## Decisões que custaram caro (NÃO reverter sem ler o diário)

1. **LLM = Cerebras `gpt-oss-120b`** (0.79s TTFB, 1M tok/dia). Groq free =
   8k tok/min → 429 em 2-3 turnos. **Llama-3.3 NUNCA**: emite tool calls
   como texto e envenena o histórico.
2. **Schema de tools todo-string** (`tools_bridge.build_tools`): Groq/
   Cerebras validam tipos estritamente e o modelo emite '5'/'true' como
   texto; as actions coagem tipos por dentro.
3. **`MinWordsUserTurnStartStrategy(min_words=3)`**: interrupção só com 3+
   palavras transcritas — eco residual de caixas dispara o VAD e cortava
   as respostas (com bot calado a própria estratégia exige só 1 palavra).
4. **Números por extenso** no prompt + ElevenLabs
   `apply_text_normalization="on"` (dígitos crus = "leitura bêbada").
5. Prompt COMPACTO (perfil resumido ~150 tokens) — contexto free da
   Cerebras é 8k.
6. Geração 1 (se mexer): Deepgram live exige `Finalize` quando o gate do
   mic fecha + KeepAlive a cada 3s sem áudio, senão frases somem/socket cai.

## Roadmap

Entregues (jul/2026): satélite de desktop (open_app + screen_look via
portal Wayland) · **Radar de Prazos** (poc/radar.py — e-mail jus.br →
data-limite determinística CPC → Calendar + briefing; baixa por voz) ·
memória semântica (memory/semantic.py, fastembed+sqlite-vec, recall
híbrido) · observabilidade (core/health.py, tool status_sistema) ·
**registry unificado** (core/registry.py — ferramenta nova = 1 entrada
em TOOLS + risco na RISK_MATRIX; tools_bridge é só ponte Pipecat).

Pendentes: modo offline (provavelmente só "modo degradado" honesto) ·
wake word (só se voltar o "sempre ouvindo") · higiene: rotacionar token
Telegram (Cerebras já rotacionada em 2026-07-08).
