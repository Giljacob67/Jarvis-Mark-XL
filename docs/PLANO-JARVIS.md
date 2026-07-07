# Plano Mestre — JARVIS de verdade

Norte estratégico: o assistente mais próximo possível do Jarvis do Homem de
Ferro, mantendo segurança, estabilidade, execução local quando possível e
arquitetura limpa. Este plano funde a auditoria técnica (ver
`DIARIO-DO-PROJETO.md`) com as 9 specs do Gilberto (jul/2026).

## Mapa: specs do Gilberto × estado real × fase

| Spec | Já existe? | Fase |
|---|---|---|
| **Presence Engine** (estados idle→error_recovery) | estados soltos na G1; nada formal | **1** |
| **Safety & Permissions Layer** (matriz de risco, modos) | parcial: `tool_requires_confirm` + gate de código (G1) | **1** |
| **Voice pipeline** — streaming/interrupção | ✅ nativo na v2 (Pipecat) | feito |
| Voice pipeline — backchannel, confirmação inteligente | não | **1** |
| Voice pipeline — perfis de voz | não | 1 (base) / 2 (troca por voz) |
| **Memory 3 camadas** (perfil/episódica/operacional) | perfil ✅; episódica bruta (SQLite G1); operacional não | **1** (keyword) / **2** (semântica) |
| **Personality Engine** | persona fixa no prompt | **1** (perfis) / 2 (adaptação por urgência/horário) |
| **HUD estado ao vivo** | orb estático no cliente v2 | **1** (estado+tool) / 2 (command center) |
| **Screen Understanding** | embrião (`screen_processor`, G1) | **2** (exige satélite desktop — servidor está no VPS) |
| **Daily Briefing rico** (fontes, modos, sob demanda) | briefing básico na proatividade G1 | **2** (junto da migração p/ v2) |
| **Autonomous Task Executor** (plano/verificação/rollback/painel) | `agent/` subutilizado | **3** |

## Fase 1 (agora) — "Presença, segurança e memória"
1. `core/presence.py` — máquina de estados com log de transições e
   listeners; sem dependência de framework (testável puro). Estados:
   idle, listening, thinking, speaking, executing_tool, observing_screen,
   proactive_waiting, error_recovery.
2. `core/permissions.py` — matriz de risco por ferramenta (sensível a
   ação: email read=baixo, send=alto), modos `read_only` /
   `supervised` (padrão) / `autonomous` (desligado por padrão),
   auditoria JSONL de toda execução, confirmação **verbal** (o LLM pede
   e re-chama com `confirm='sim'`).
3. `memory/layered.py` — 3 camadas: perfil (arquivos existentes),
   episódica (JSONL + busca por palavras, com esquecimento/tombstone),
   operacional (estado corrente + últimas ações). Ferramentas novas:
   `remember`, `forget`, `recall`, `context_summary`.
4. Voz: backchannel moderado ("Um momento.") em ferramentas lentas;
   confirmação inteligente via permissions; perfis de personalidade em
   `config/personality.json` integrados ao prompt.
5. HUD: cliente v2 ganha data channel — estado ao vivo (cor/rótulo por
   estado) + ferramenta em execução.
6. Testes puros para presence/permissions/memory; docs atualizados.

## Fase 2 (médio) — "Onisciente e advogado"
Migração Telegram+proatividade p/ servidor v2 · Briefing rico (fontes:
agenda, Gmail, clima, projetos; modos curto/médio/completo; sob demanda;
histórico) · Memória semântica local (sqlite-vec) + injeção por
relevância · Satélite de desktop (RPC tailnet): open_app, screen
understanding (OCR + visão, modo privado com pausa), wake word local ·
Radar de prazos jurídico (e-mails de tribunal → Calendar + briefing) ·
Personality adaptativa (urgência/horário/risco) · Observabilidade
(latência/custo/erros) · Modo offline/privado (whisper+Ollama+Kokoro
locais) · Registry unificado de ferramentas (aposenta dispatcher da G1).

## Fase 3 (longo) — "O Jarvis completo"
Task Executor autônomo (objetivo→plano→passos→verificação→rollback,
painel de tarefas, logs legíveis) sobre `agent/` renovado · Automação
jurídica profunda (consulta processual, minutas) · Casa/escritório
inteligente (kasa) · Command center fullscreen + overlay compacto ·
Speech-to-speech quando houver pt-BR · Multi-usuário/vozes.

## Princípios inegociáveis
- Não quebrar o que o usuário validou (voz fluida v2, tools, VPS).
- Segurança primeiro: nada de alto risco sem confirmação; autônomo total
  sempre opt-in; auditoria de tudo; segredos jamais no git.
- Local quando possível: fase 2 traz o perfil offline; dados sensíveis
  têm caminho que não sai das máquinas dele.
- Commits pequenos e temáticos; testes junto; diário atualizado.
