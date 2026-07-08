"""Memória semântica — índice sqlite-vec e recall híbrido.

O embedder falso projeta em eixos temáticos fixos, então os testes são
determinísticos e rodam sem fastembed. O sqlite-vec em si é exigido
(pytest.importorskip) — no Python do sistema sem a lib, pulam.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("sqlite_vec")

from memory.layered import LayeredMemory
from memory.semantic import DIMS, SemanticIndex

# eixos temáticos: (palavras-gatilho, dimensão)
_AXES = [({"execucao", "fiscal", "divida", "tributo", "balneario"}, 0),
         ({"aniversario", "bolo", "festa", "presente"}, 1),
         ({"audiencia", "forum", "juiz", "processo"}, 2)]


def fake_embed(texts: list[str]) -> list[list[float]]:
    out = []
    for t in texts:
        import unicodedata
        t = unicodedata.normalize("NFKD", t.lower())
        t = "".join(c for c in t if not unicodedata.combining(c))
        v = [0.0] * DIMS
        v[3] = 0.1                                   # base comum
        for words, dim in _AXES:
            v[dim] += sum(2.0 for w in words if w in t)
        n = math.sqrt(sum(x * x for x in v))
        out.append([x / n for x in v])
    return out


def _index(tmp_path) -> SemanticIndex:
    return SemanticIndex(tmp_path / "vec.db", embed_fn=fake_embed)


def test_index_search_similaridade(tmp_path):
    idx = _index(tmp_path)
    idx.index(1.0, "execução fiscal do município, dívida tributária")
    idx.index(2.0, "comprar bolo de aniversário para a festa")
    res = idx.search("aquela dívida da execução fiscal", limit=2)
    assert res[0][1] == 1.0                    # mais similar primeiro
    assert res[0][0] > 0.8 > res[1][0]         # e bem acima do off-topic


def test_index_dedupe_remove_prune(tmp_path):
    idx = _index(tmp_path)
    idx.index(1.0, "execução fiscal")
    idx.index(1.0, "execução fiscal")          # mesmo ts: ignora
    idx.index(2.0, "festa de aniversário")
    assert len(idx.search("qualquer", limit=10)) == 2
    idx.remove([1.0])
    assert [ts for _, ts in idx.search("execução fiscal", limit=10)] == [2.0]
    assert idx.prune({999.0}) == 1             # 2.0 não está vivo → sai
    assert idx.search("festa", limit=10) == []


def test_recall_hibrido_sem_termo_exato(tmp_path):
    m = LayeredMemory(episodic_path=tmp_path / "epi.jsonl", embed_fn=fake_embed)
    m.remember("execução fiscal de Balneário: propor acordo até sexta")
    m.remember("comprar presente de aniversário da Maria")
    # consulta SEM palavra em comum com o alvo (tirando stopwords):
    # 'dívida'/'tributo' só casam pelo eixo semântico
    r = m.recall("aquela dívida de tributo")
    assert "Balneário" in r and "aniversário" not in r


def test_recall_forget_some_do_indice(tmp_path):
    m = LayeredMemory(episodic_path=tmp_path / "epi.jsonl", embed_fn=fake_embed)
    m.remember("execução fiscal de Balneário")
    m.forget("execução fiscal")
    assert "Não tenho" in m.recall("dívida tributo")


def test_backfill_de_acervo_antigo(tmp_path):
    p = tmp_path / "epi.jsonl"
    m1 = LayeredMemory(episodic_path=p, embed_fn=fake_embed)
    object.__setattr__(m1, "_sem_tried", True)     # simula era pré-semântica
    m1.remember("execução fiscal de Balneário, prazo na sexta")
    m2 = LayeredMemory(episodic_path=p, embed_fn=fake_embed)
    r = m2.recall("dívida de tributo")             # backfill no primeiro uso
    assert "Balneário" in r
