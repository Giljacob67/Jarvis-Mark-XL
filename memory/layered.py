"""
JARVIS — Memória em três camadas.

  PERFIL       quem o usuário é (estável): memory/user_profile.md +
               long_term.json — já existentes, esta camada só os expõe.
  EPISÓDICA    o que aconteceu: notas ditas ("lembre disso"), decisões,
               ferramentas executadas. JSONL append-only com tombstones
               para esquecimento (memory/episodic.jsonl).
  OPERACIONAL  o agora: estado de presença, últimas ações, pendências.
               Vive em RAM (reconstruída a cada boot — é efêmera por design).

Busca (Fase 2): HÍBRIDA — palavras + recência (precisa) combinada com
similaridade semântica local (memory/semantic.py: fastembed + sqlite-vec),
então "aquela execução de Balneário" acha "execução fiscal de Balneário
Arroio do Silva" sem termo exato. Sem as dependências instaladas, degrada
sozinha para a busca por palavras da Fase 1.

Política de privacidade e esquecimento:
  - "esqueça X" marca tombstone — o item some de todas as buscas;
  - purge() remove fisicamente os tombstonados (LGPD-friendly);
  - nada desta camada vai para o git (memory/*.json* está no .gitignore);
  - só memórias RELEVANTES à pergunta entram no prompt (via ferramenta
    recall), nunca o acervo inteiro.
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from pathlib import Path

from core.logger import get_logger

log = get_logger("memory.layered")

_BASE = Path(__file__).resolve().parent
EPISODIC_PATH = _BASE / "episodic.jsonl"
PROFILE_MD    = _BASE / "user_profile.md"


def _norm(text: str) -> list[str]:
    """Tokens minúsculos sem acento (busca acento-insensível pt-BR)."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]{3,}", text)


_STOP = set(_norm("que com para uma dos das por sobre isso essa este aquele "
                  "quando onde como você voce meu minha seu sua ele ela"))


class LayeredMemory:
    def __init__(self, episodic_path: Path | None = None, embed_fn=None):
        self._path = episodic_path or EPISODIC_PATH
        self._lock = threading.Lock()
        # operacional (efêmera)
        self._op_state: dict[str, str] = {}
        self._op_actions: list[tuple[float, str]] = []
        # semântica (acessório lazy; None = indisponível/desligada)
        self._embed_fn = embed_fn
        self._sem = None
        self._sem_tried = False

    def _semantic(self):
        if not self._sem_tried:
            self._sem_tried = True
            try:
                from memory.semantic import open_index
                db = self._path.parent / (self._path.stem + "_vec.db")
                self._sem = open_index(db, self._embed_fn)
                if self._sem:
                    self._sem.sync(self._load())   # backfill do acervo
            except Exception as e:
                log.warning("índice semântico falhou (%s) — só palavras", e)
                self._sem = None
        return self._sem

    # ── PERFIL ───────────────────────────────────────────────────────────
    def profile_summary(self, max_chars: int = 600) -> str:
        try:
            text = PROFILE_MD.read_text(encoding="utf-8")
            # primeiro bloco (identidade) é o resumo natural do perfil
            head = text.split("##", 2)
            core = (head[1] if len(head) > 1 else text)
            return ("## " + core).strip()[:max_chars]
        except Exception:
            return ""

    # ── EPISÓDICA ────────────────────────────────────────────────────────
    def _load(self) -> list[dict]:
        try:
            entries = []
            with open(self._path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
            # aplica tombstones
            dead = {e["target_ts"] for e in entries if e.get("kind") == "tombstone"}
            return [e for e in entries
                    if e.get("kind") != "tombstone" and e.get("ts") not in dead]
        except FileNotFoundError:
            return []
        except Exception as e:
            log.warning("episódica ilegível: %s", e)
            return []

    def _append(self, entry: dict) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def remember(self, text: str, kind: str = "note") -> str:
        """'Lembre disso' — grava um episódio."""
        text = (text or "").strip()
        if not text:
            return "Nada para lembrar."
        ts = time.time()
        self._append({"ts": ts, "kind": kind, "text": text,
                      "date": time.strftime("%Y-%m-%d %H:%M")})
        log.info("episódica += [%s] %s", kind, text[:60])
        sem = self._semantic()
        if sem:
            try:
                sem.index(ts, text)
            except Exception as e:
                log.warning("indexação semântica falhou: %s", e)
        return f"Memorizado: {text[:80]}"

    # similaridade mínima para um resultado PURAMENTE semântico entrar
    # (abaixo disso o vizinho mais próximo é só o menos distante do acervo).
    # Calibrado com o MiniLM multilíngue em pt-BR: relevantes ≈0.40-0.45,
    # mesmo domínio mas outro assunto ≈0.25-0.30, off-topic <0.10.
    _SEM_FLOOR = 0.35

    def recall(self, query: str, limit: int = 4) -> str:
        """'O que você sabe sobre X' — híbrida: palavras + semântica."""
        q = [t for t in _norm(query) if t not in _STOP]
        if not q:
            return "Preciso de um termo para buscar."
        now = time.time()
        alive = self._load()
        sims: dict[float, float] = {}
        sem = self._semantic()
        if sem:
            try:
                sims = {ts: s for s, ts in sem.search(query, limit=limit * 3)}
            except Exception as e:
                log.warning("busca semântica falhou: %s", e)
        scored = []
        for e in alive:
            toks = set(_norm(e.get("text", "")))
            hits = sum(1 for t in q if t in toks)
            sim = sims.get(e.get("ts"), 0.0)
            if not hits and sim < self._SEM_FLOOR:
                continue
            age_days = (now - e.get("ts", now)) / 86400
            score = (hits + sim * 2.0 +
                     max(0.0, 1.0 - age_days / 30) * 0.5)   # recência leve
            scored.append((score, e))
        if not scored:
            return f"Não tenho nada registrado sobre '{query}'."
        scored.sort(key=lambda x: -x[0])
        lines = [f"{e['date']}: {e['text']}" for _, e in scored[:limit]]
        return "Encontrei na memória: " + " | ".join(lines)

    def forget(self, query: str) -> str:
        """'Esqueça isso' — tombstone em tudo que casa com a busca."""
        q = [t for t in _norm(query) if t not in _STOP]
        if not q:
            return "Preciso saber o que esquecer."
        victims = [e for e in self._load()
                   if any(t in set(_norm(e.get("text", ""))) for t in q)]
        for e in victims:
            self._append({"kind": "tombstone", "target_ts": e["ts"],
                          "ts": time.time()})
        sem = self._semantic()
        if sem and victims:
            try:      # esquecido de verdade: some também do índice vetorial
                sem.remove([e["ts"] for e in victims])
            except Exception as e:
                log.warning("remoção semântica falhou: %s", e)
        n = len(victims)
        log.info("esquecidos %d episódios (query=%s)", n, query)
        return (f"Esquecido: {n} registro{'s' if n != 1 else ''} sobre "
                f"'{query}'." if n else f"Nada encontrado sobre '{query}'.")

    def purge(self) -> int:
        """Remove fisicamente os tombstonados (compacta o arquivo)."""
        with self._lock:
            alive = self._load()
            with open(self._path, "w", encoding="utf-8") as f:
                for e in alive:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
        sem = self._semantic()
        if sem:
            try:
                sem.prune({e["ts"] for e in alive})
            except Exception as e:
                log.warning("prune semântico falhou: %s", e)
        return len(alive)

    # ── OPERACIONAL ──────────────────────────────────────────────────────
    def op_set(self, key: str, value: str) -> None:
        self._op_state[key] = value

    def op_action(self, text: str) -> None:
        self._op_actions.append((time.time(), text))
        if len(self._op_actions) > 50:
            self._op_actions = self._op_actions[-25:]

    def context_summary(self) -> str:
        """'Resuma meu contexto atual' — snapshot falável do agora."""
        parts = []
        if self._op_state:
            parts.append("; ".join(f"{k}: {v}" for k, v in self._op_state.items()))
        if self._op_actions:
            last = [t for _, t in self._op_actions[-5:]]
            parts.append("últimas ações: " + "; ".join(last))
        recent = self._load()[-3:]
        if recent:
            parts.append("memórias recentes: " +
                         " | ".join(e["text"][:60] for e in recent))
        return ". ".join(parts) if parts else \
            "Sem contexto operacional registrado nesta sessão."


# Singleton do processo
_mem: LayeredMemory | None = None
_mem_lock = threading.Lock()


def get_memory() -> LayeredMemory:
    global _mem
    with _mem_lock:
        if _mem is None:
            _mem = LayeredMemory()
        return _mem


# ── Schemas das ferramentas de memória (consumidos pelo tools_bridge) ────
MEMORY_TOOL_SCHEMAS = [
    {"name": "remember",
     "description": "Grava algo que o usuário pediu para lembrar ('lembre disso', "
                    "'anota que', 'não esqueça que'). Use o texto do fato, não a ordem.",
     "properties": {"text": {"type": "string", "description": "O fato a memorizar"}},
     "required": ["text"]},
    {"name": "recall",
     "description": "Busca na memória de longo prazo ('o que você sabe sobre X', "
                    "'do que combinamos sobre...').",
     "properties": {"query": {"type": "string", "description": "Termo a buscar"}},
     "required": ["query"]},
    {"name": "forget",
     "description": "Esquece registros da memória ('esqueça isso/o que falei sobre X').",
     "properties": {"query": {"type": "string", "description": "O que esquecer"}},
     "required": ["query"]},
    {"name": "context_summary",
     "description": "Resume o contexto atual da sessão ('resuma meu contexto', "
                    "'o que está acontecendo agora').",
     "properties": {}, "required": []},
]
