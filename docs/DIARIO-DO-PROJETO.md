# Diário do Projeto — Jarvis Mark XL

Registro curado da colaboração Gilberto + Claude (jul/2026). Sem segredos.
Objetivo: qualquer sessão futura (em qualquer máquina) retomar com contexto
completo. Complementa o `CLAUDE.md` (operacional) com o **porquê** das coisas.

---

## Capítulo 1 — Auditoria e resgate da Geração 1 (02–03/jul)

Fork de FatihMakes/Mark-XL: app Qt com Mic → STT → LLM → tools → TTS.
Auditoria completa (2 fases) encontrou e corrigiu, entre dezenas de itens:

- **Voz falava só a 1ª frase** de cada resposta (descarte deliberado no
  streaming) — o bug mais visível de todos.
- Zero sanitização pré-TTS (markdown/emoji/URLs falados) → `core/tts_text.py`.
- `TTSPlayer.stop()` sem chamadores e quebrado p/ 2 dos 3 engines → evento
  de cancelamento + interrupção limpa + fallback de engine (ElevenLabs→Edge).
- Pipeline TTS reestruturado: síntese/playback em 2 estágios com
  `AudioOutput` persistente (fim dos gaps entre frases e pops do Kokoro).
- **Deepgram live**: gate do mic congelava o stream → frases costuradas,
  finais com 15-38s de atraso. Fix: `Finalize` na transição do gate +
  acúmulo de segmentos `is_final` + `UtteranceEnd` + KeepAlive 3s +
  reconexão com backoff. (Sintomas espetaculares: "Jarbes", "Você", eco.)
- Timeout de ferramenta era falso (congelava o pipeline); QMessageBox fora
  da GUI thread; gate `allow_code_execution` ignorado por 3 ferramentas;
  threads de comando morriam silenciosamente — tudo corrigido.
- **Alucinação Marvel** ("compromissos da Natasha Romanoff"): prompts sem
  grounding + roteamento sem tools p/ perguntas de agenda. Corrigido com
  regras de grounding e roteamento (`compromiss|tarefa|reuni` → tools).
- Config mista: `llm_provider=groq` com modelo do Ollama Cloud → 404 em
  toda tool. Auto-correção de nomes cross-provider no `normalize_model_name`.

Entregas paralelas: motor de proatividade (briefing 08:30, checagens de
e-mail, lembretes 60/15min — `core/proactive.py`), agenda local JSON p/
Linux sem khal, Gmail API + Google Calendar OAuth (`scripts/setup_google.py`),
bot Telegram (`core/telegram_bot.py`, dono id 7995994992), dashboard PWA,
perfil do usuário injetado em todo turno, keyword boost no Deepgram.

## Capítulo 2 — O pivô (03–04/jul)

Tentativa de AEC via PipeWire (module-echo-cancel + roteamento pw-metadata)
tecnicamente funcionou no roteamento mas degradou a experiência (streams no
nó errado → mic surdo; sem referência → eco total). Usuário: "estamos só
apagando incêndio". Pesquisa de mercado → conclusão: o pipeline artesanal
reconstruía o que Pipecat/LiveKit já resolveram; echo cancellation pertence
ao CLIENTE (navegador/WebRTC), não ao servidor.

**Decisão: Geração 2 sobre Pipecat 1.4** (branch `pipecat-poc`). POC em
uma sessão: navegador WebRTC + Silero VAD + barge-in nativo + Deepgram +
Groq + ElevenLabs. Métricas validadas: voz→voz ~1s, TTFB LLM ~300ms,
interrupções limpas. Usuário: "aparentemente ficou muito bom".

## Capítulo 3 — Fase 2: o cérebro na voz nova (04–05/jul)

`poc/tools_bridge.py`: actions existentes expostas via function calling
nativo (schemas de `core/tools.py`, fonte única; handler async roda a
action em thread; hook `say()` p/ fala espontânea — timer avisa por voz).

Calibragem que doeu (cada uma tem commit próprio):
1. Cliente pré-pronto sem constraints de áudio → loop de eco → cliente
   próprio (`poc/client.html`) com AEC/NS/AGC explícitos + suporte file://.
2. Groq valida tipos estritamente → schema todo-string.
3. **Llama-3.3 emite tool call como TEXTO** (`</function>` foi pro TTS!) e
   envenena o histórico → gpt-oss-120b sempre.
4. Groq free tier 8k tok/min → 429 em 2-3 turnos → prompt compacto e,
   como Dev Tier estava indisponível, **benchmark real**: Cerebras 0.79s
   vs Groq 1.10s vs Ollama Cloud 1.71s → **Cerebras** (1M tok/dia, mesmo
   modelo, serviço nativo no Pipecat).
5. Eco residual de caixas disparava VAD → interrupções fantasma em cascata
   → respostas cortadas/repetidas → `MinWordsUserTurnStartStrategy(3)`.
6. "Leitura bêbada" de números → por extenso no prompt + normalização
   ElevenLabs; números longos (processo/CNPJ) referidos de forma curta.

**05/jul: usuário validou a Fase 2** ("ficou muito bom, um pequeno
delayzinho mas nada que atrapalhe").

## Capítulo 4 — Fase 3: VPS (05/jul, 95% concluída)

VPS Hetzner `ubuntu-8gb-hel1-1` (4 vCPU, 8GB): código em `/opt/jarvis`,
venv, segredos via scp (chmod 600), serviço systemd `jarvis-voice`
(ativo, porta 7860 localhost), `open_app` auto-removida em headless.

**ÚNICO PASSO PENDENTE**: usuário habilitar Tailscale Serve no link
`https://login.tailscale.com/f/serve?node=no7TucuVfG11CNTRL` → depois
rodar no VPS: `tailscale serve --bg http://127.0.0.1:7860` → URL final
`https://ubuntu-8gb-hel1-1.tail54aaa6.ts.net` (HTTPS obrigatório p/ mic
fora de localhost). iPhone/Mac: abrir a URL + "Adicionar à Tela de Início".

## Pendências e próximos capítulos

- [ ] Clique do Tailscale Serve (acima) + validar da tailnet
- [ ] Rotacionar chaves Cerebras e Telegram (passaram pelo chat)
- [ ] Fase 3b: satélite de desktop (RPC tailnet) p/ open_app e afins
- [ ] Migrar Telegram + proatividade do app antigo p/ o servidor v2
- [ ] Aposentar loop de voz do app Qt (main.py) — manter só painel/legado
- [ ] **Fase 4 — Radar de prazos jurídico** (maior valor): parser de
      e-mails de tribunal (Projudi/PJe/e-Proc) → extrai prazo/processo →
      Google Calendar + briefing matinal + lembretes escalonados
- [ ] Poda de histórico na v2 (contexto 8k da Cerebras em sessões longas)
- [ ] Merge `pipecat-poc` → `main` quando a v2 absorver Telegram/proatividade

## Convenções de trabalho

Commits temáticos em pt-BR com o porquê no corpo; push direto autorizado;
diagnóstico SEMPRE por log/evidência antes de corrigir (o usuário cola o
log da UI, ou lemos o do servidor ao vivo); testes headless com APIs reais
antes de entregar ao usuário; o usuário testa por voz e reporta em linguagem
de sintoma ("bêbado", "louco", "gaguejando") — os sintomas dele são precisos.
