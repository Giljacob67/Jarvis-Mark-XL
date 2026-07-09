# Auditoria arquitetural — Jarvis Mark XL

Data: 2026-07-09. Branch auditado: `pipecat-poc`.

## Resumo executivo

O projeto tem duas gerações no mesmo repositório. A Geração 1 (`main.py`,
PyQt/PortAudio) está aposentada e deve ser tratada como legado. A Geração 2 é
o caminho ativo: `poc/bot.py` com Pipecat/WebRTC, cliente web em `poc/voz.html`,
registry unificado em `core/registry.py`, ponte de tools em `poc/tools_bridge.py`
e serviços 24/7 em `poc/services.py`.

## Arquitetura atual

- **Voz ativa**: navegador/WebRTC → Deepgram STT → Cerebras/Groq LLM →
  ElevenLabs/Edge TTS → áudio WebRTC.
- **Ferramentas**: `core/registry.py` é a fonte de handlers/schemas; a ponte
  `poc/tools_bridge.py` aplica permissões, auditoria, coerção de schema e
  execução em thread.
- **Segurança**: `core/permissions.py` classifica risco por ferramenta/ação,
  suporta `read_only`, `supervised` e `autonomous`, e registra auditoria JSONL.
- **Memória**: `memory/layered.py` organiza perfil, episódica e operacional;
  `memory/semantic.py` adiciona recall vetorial quando dependências existem.
- **Presença/HUD**: `core/presence.py` mantém estado; `poc/voz.html` exibe
  estado ao vivo; a rota `GET /api/jarvis/hud` fornece fallback HTTP estável.

## Riscos observados

- **Legado volumoso**: `main.py` ainda contém muito código antigo; mudanças na
  voz devem mirar a v2 para não reacender bugs já aposentados.
- **Data channel WebRTC instável**: em alguns cenários SCTP/Tailscale/iOS, o
  canal abre tarde ou não abre. O HUD precisa de fallback HTTP.
- **Segredos em auditoria**: argumentos de ferramentas podem conter tokens; a
  auditoria deve redigir chaves sensíveis sempre.
- **Sessões longas**: contexto Cerebras de 8k ainda pede poda de histórico no
  pipeline v2.

## Plano técnico em fases

### Fase 1 — Presença, segurança e operação visível

- Presence Engine com estados explícitos e histórico.
- Permissions Layer obrigatório em toda tool, com confirmação verbal para alto
  risco e auditoria sem segredos.
- Voz com respostas curtas, streaming, interrupção calibrada, backchannels e
  confirmação natural.
- Memory Manager em camadas: perfil, episódica e operacional.
- HUD com estado atual, tarefa em execução e logs, via data channel + fallback
  HTTP.

### Fase 2 — Contexto real e percepção

- Poda de histórico da v2 e recall relevante antes de cada turno.
- Screen understanding com satélite desktop, modo privado e trilha de auditoria.
- Briefing rico com fontes explícitas, modos e histórico consultável.
- Personalidade adaptativa por urgência, horário e contexto de reunião.

### Fase 3 — Executor autônomo seguro

- Executor objetivo→plano→passos→verificação→rollback sobre `agent/`.
- Painel de tarefas, cancelamento, logs legíveis e checkpoints.
- Playbooks jurídicos especializados para rotinas de prazo, minuta e consulta.

### Fase 4 — Presença contínua multimodal

- Modo degradado offline honesto.
- Wake word local apenas se a experiência voltar a ser sempre ouvindo.
- Command center/overlay com visão de tela, tarefas, memória e saúde do sistema.
