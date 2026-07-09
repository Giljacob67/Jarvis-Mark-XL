"""
MARK XL — Semantic memory retrieval (local-first).

Ranks stored memories / past conversation turns by relevance to the current
query so the assistant can recall the *right* fact, not just the most recent.

Two backends, both optional and fail-closed:
  * If ``sentence-transformers`` is installed, embeddings are used (multilingual
    MiniLM) for true semantic ranking.
  * Otherwise a pure-stdlib lexical TF-IDF cosine scorer is used — no extra
    dependencies, works fully offline.

All embedding/linear-algebra imports are lazy so this module imports cleanly
even when numpy / sentence-transformers are absent.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from core.logger import get_logger

log = get_logger("semantic")

_STOP = set(
    "a o e de da do das dos em no na nos nas um uma uns umas para por com sem "
    "que se ao aos as os é são ser foi era com foi num numa nesta neste "
    "você voce seu sua seus suas meu minha meus minhas este essa esse "
    "isso aquela aquele quando onde quem como qual quais porque pois já "
    "mais menos sobre entre após apos ate até pelo pela pelas pelos numa".split()
)
_TOKEN = re.compile(r"[a-zà-áâãéêíóôõúç0-9]+")

_embed_model = None


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").lower())
            if t not in _STOP and len(t) > 1]


def _tfidf_vector(text: str, idf: dict) -> dict:
    counts = Counter(_tokens(text))
    if not counts:
        return {}
    n = sum(counts.values())
    return {w: (1 + math.log(counts[w])) * idf.get(w, 1.0) for w in counts}


def _cosine(a: dict, b: dict) -> float:
    common = set(a) & set(b)
    if not common:
        return 0.0
    num = sum(a[w] * b[w] for w in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return num / (na * nb) if na and nb else 0.0


def _embed(text: str):
    """Return an embedding (list/np.array) or None if unavailable."""
    global _embed_model
    try:
        from sentence_transformers import SentenceTransformer
    except Exception:
        return None
    try:
        if _embed_model is None:
            _embed_model = SentenceTransformer(
                "paraphrase-multilingual-MiniLM-L12-v2")
        return _embed_model.encode(text)
    except Exception as e:
        log.warning("embedding falhou (%s) — usando lexical", e)
        return None


def retrieve_relevant(query: str, candidates: list[str], k: int = 6,
                      max_chars: int = 1200) -> str:
    """Rank ``candidates`` by relevance to ``query`` and return the top-k
    concatenated (bounded to ``max_chars``). Returns '' when empty.
    """
    candidates = [c for c in candidates if c and c.strip()]
    if not candidates or not query or not query.strip():
        return ""

    q_emb = _embed(query)
    if q_emb is not None:
        try:
            import numpy as np
            scored = []
            for i, c in enumerate(candidates):
                c_emb = _embed(c)
                score = float(np.dot(q_emb, c_emb)) if c_emb is not None else 0.0
                scored.append((score, i))
        except Exception:
            q_emb = None  # fall through to lexical

    if q_emb is None:
        df: Counter = Counter()
        for doc in candidates:
            for w in set(_tokens(doc)):
                df[w] += 1
        n = len(candidates)
        idf = {w: math.log((n + 1) / (c + 1)) + 1 for w, c in df.items()}
        qv = _tfidf_vector(query, idf)
        scored = [(_cosine(qv, _tfidf_vector(c, idf)), i) for i, c in enumerate(candidates)]

    scored.sort(reverse=True)
    top = [candidates[i] for _, i in scored[:k] if _ > 0]
    if not top:
        return ""
    text = "\n\n".join(top)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0]
    return text
