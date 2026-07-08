"""
JARVIS — índice semântico da memória episódica (Fase 2).

Embeddings locais (fastembed/ONNX, paraphrase-multilingual-MiniLM-L12-v2,
384 dims, ~82ms/frase em CPU) persistidos em sqlite-vec — nada sai da
máquina e nada é re-embedado no boot.

O índice é um ACESSÓRIO da episódica (memory/layered.py), nunca a fonte:
guarda só (ts → vetor); texto, tombstones e LGPD continuam no JSONL.
Qualquer falha aqui (dependência ausente, modelo não baixado) degrada
silenciosamente para a busca por palavras de sempre.

`embed_fn` é injetável para testes (os reais exigem fastembed instalado).
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Callable

from core.logger import get_logger

log = get_logger("memory.semantic")

EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DIMS = 384

_model = None
_model_lock = threading.Lock()


def _default_embed(texts: list[str]) -> list[list[float]]:
    """Singleton do modelo ONNX (carga ~1-2s depois do download inicial).

    Normaliza na saída: o fastembed devolve o mean pooling CRU deste
    modelo, e toda a matemática do índice (L2 → cosseno) exige norma 1.
    """
    global _model
    with _model_lock:
        if _model is None:
            from fastembed import TextEmbedding
            _model = TextEmbedding(EMBED_MODEL)
    import math
    out = []
    for v in _model.embed(texts):
        v = list(map(float, v))
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


class SemanticIndex:
    def __init__(self, db_path: Path,
                 embed_fn: Callable[[list[str]], list[list[float]]] | None = None):
        self._path = Path(db_path)
        self._embed = embed_fn or _default_embed
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None

    def _conn(self) -> sqlite3.Connection:
        if self._db is None:
            import sqlite_vec
            self._path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self._path, check_same_thread=False)
            db.enable_load_extension(True)
            sqlite_vec.load(db)
            db.enable_load_extension(False)
            db.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS vec "
                       f"USING vec0(embedding float[{DIMS}])")
            db.execute("CREATE TABLE IF NOT EXISTS map ("
                       "rowid INTEGER PRIMARY KEY, ts REAL UNIQUE)")
            self._db = db
        return self._db

    # ── escrita ───────────────────────────────────────────────────────────
    def index(self, ts: float, text: str) -> None:
        import sqlite_vec
        vec = self._embed([text])[0]
        with self._lock:
            db = self._conn()
            cur = db.execute("INSERT OR IGNORE INTO map(ts) VALUES (?)", (ts,))
            if cur.rowcount == 0:          # já indexado
                return
            db.execute("INSERT INTO vec(rowid, embedding) VALUES (?, ?)",
                       (cur.lastrowid, sqlite_vec.serialize_float32(vec)))
            db.commit()

    def remove(self, ts_list: list[float]) -> None:
        if not ts_list:
            return
        with self._lock:
            db = self._conn()
            ph = ",".join("?" * len(ts_list))
            rows = [r[0] for r in db.execute(
                f"SELECT rowid FROM map WHERE ts IN ({ph})", ts_list)]
            if rows:
                ph2 = ",".join("?" * len(rows))
                db.execute(f"DELETE FROM vec WHERE rowid IN ({ph2})", rows)
                db.execute(f"DELETE FROM map WHERE rowid IN ({ph2})", rows)
                db.commit()

    def prune(self, alive_ts: set[float]) -> int:
        """Remove do índice tudo que não está mais vivo na episódica."""
        with self._lock:
            db = self._conn()
            dead = [r[0] for r in db.execute("SELECT ts FROM map")
                    if r[0] not in alive_ts]
        self.remove(dead)
        return len(dead)

    def sync(self, entries: list[dict], max_batch: int = 200) -> int:
        """Backfill: indexa episódios que ainda não têm vetor."""
        with self._lock:
            db = self._conn()
            have = {r[0] for r in db.execute("SELECT ts FROM map")}
        todo = [e for e in entries
                if e.get("ts") not in have and e.get("text")][:max_batch]
        for e in todo:
            self.index(e["ts"], e["text"])
        if todo:
            log.info("semântica: backfill de %d episódios", len(todo))
        return len(todo)

    # ── busca ─────────────────────────────────────────────────────────────
    def search(self, query: str, limit: int = 8) -> list[tuple[float, float]]:
        """[(similaridade 0..1, ts)] — cosseno via distância do sqlite-vec."""
        import sqlite_vec
        vec = self._embed([query])[0]
        with self._lock:
            db = self._conn()
            rows = db.execute(
                "SELECT m.ts, v.distance FROM vec v JOIN map m "
                "ON m.rowid = v.rowid WHERE v.embedding MATCH ? AND k = ? "
                "ORDER BY v.distance",
                (sqlite_vec.serialize_float32(vec), limit)).fetchall()
        # distância L2 de vetores normalizados: d² = 2(1 - cos) → cos = 1 - d²/2
        return [(max(0.0, 1.0 - (d * d) / 2.0), ts) for ts, d in rows]


def open_index(db_path: Path,
               embed_fn=None) -> SemanticIndex | None:
    """Fábrica tolerante: None se as dependências não existirem no ambiente."""
    try:
        import sqlite_vec  # noqa: F401
        if embed_fn is None:
            import fastembed  # noqa: F401
        return SemanticIndex(db_path, embed_fn)
    except ImportError as e:
        log.info("semântica indisponível (%s) — recall segue por palavras", e)
        return None
