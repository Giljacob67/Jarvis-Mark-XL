"""Radar de Prazos — testes: calendário forense, contagem CPC, extração e
persistência. A matemática de prazo é o coração: erro aqui custa caro."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from poc import radar
from poc.radar import (compute_deadline, holidays, is_business_day,
                       _easter, _in_recess, _parse_json, _build_entry,
                       pending, speakable)


# ── calendário forense ────────────────────────────────────────────────────

def test_easter_known_years():
    assert _easter(2026) == date(2026, 4, 5)
    assert _easter(2025) == date(2025, 4, 20)
    assert _easter(2024) == date(2024, 3, 31)


def test_holidays_2026():
    hs = holidays(2026)
    assert date(2026, 4, 3) in hs      # sexta-feira santa
    assert date(2026, 2, 17) in hs     # terça de carnaval
    assert date(2026, 6, 4) in hs      # corpus christi
    assert date(2026, 11, 20) in hs    # consciência negra (nacional)
    assert date(2026, 5, 10) in hs     # aniversário de Maringá


def test_business_day_rules():
    assert is_business_day(date(2026, 7, 8))          # quarta comum
    assert not is_business_day(date(2026, 7, 11))     # sábado
    assert not is_business_day(date(2026, 9, 7))      # feriado nacional
    assert not is_business_day(date(2026, 12, 22))    # recesso forense
    assert not is_business_day(date(2027, 1, 15))     # recesso (janeiro)
    assert is_business_day(date(2027, 1, 21))         # fim do recesso


def test_recess_window():
    assert _in_recess(date(2026, 12, 20))
    assert _in_recess(date(2027, 1, 20))
    assert not _in_recess(date(2026, 12, 19))
    assert not _in_recess(date(2027, 1, 21))


# ── contagem de prazo (CPC arts. 219, 224) ───────────────────────────────

def test_deadline_uteis_simple():
    # ciência qua 08/07/2026, 5 dias úteis → qui 09, sex 10, seg 13,
    # ter 14, qua 15 (sáb/dom pulados; exclui o dia do começo)
    assert compute_deadline(date(2026, 7, 8), 5) == date(2026, 7, 15)


def test_deadline_uteis_15_dias():
    # prazo clássico de contestação: 15 dias úteis a partir de qua 08/07
    assert compute_deadline(date(2026, 7, 8), 15) == date(2026, 7, 29)


def test_deadline_uteis_pula_feriado():
    # ciência sex 04/09/2026; seg 07/09 é feriado → 2 úteis = ter 08 e qua 09
    assert compute_deadline(date(2026, 9, 4), 2) == date(2026, 9, 9)


def test_deadline_uteis_atravessa_recesso():
    # ciência ter 15/12/2026: qua 16, qui 17, sex 18 = 3 úteis; 19/dez cai
    # no sábado e 20/dez–20/jan é recesso → retoma qui 21/01 (4º) e o 5º
    # dia útil cai na sex 22/01/2027
    assert compute_deadline(date(2026, 12, 15), 5) == date(2027, 1, 22)


def test_deadline_corridos_prorroga_fim_de_semana():
    # 10 corridos de qui 02/07/2026 → dom 12/07 → prorroga p/ seg 13 (art. 224 §1º)
    assert compute_deadline(date(2026, 7, 2), 10, "corridos") == date(2026, 7, 13)


# ── extração ──────────────────────────────────────────────────────────────

def test_parse_json_tolerante():
    assert _parse_json('bla {"tem_prazo": true, "prazo_dias": 15} bla') == \
        {"tem_prazo": True, "prazo_dias": 15}
    assert _parse_json("sem json aqui") is None
    assert _parse_json('{"quebrado": ') is None


def test_build_entry_calcula_e_explicita():
    em = {"id": "abc123"}
    info = {"tem_prazo": True, "processo": "0001234-56.2026.8.16.0017",
            "tribunal": "TJPR", "ato": "contestação",
            "data_ciencia": "2026-07-08", "prazo_dias": 15,
            "contagem": "uteis", "data_limite_explicita": "",
            "resumo": "Contestar na execução fiscal."}
    e = _build_entry(em, info)
    assert e["data_limite"] == "2026-07-29"
    assert e["status"] == "aberto" and not e["explicita"]

    info["data_limite_explicita"] = "2026-08-03"
    e2 = _build_entry(em, info)
    assert e2["data_limite"] == "2026-08-03" and e2["explicita"]


# ── persistência e consulta ───────────────────────────────────────────────

def _write_prazos(tmp_path, monkeypatch, entries):
    p = tmp_path / "prazos.jsonl"
    p.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n"
                         for e in entries), encoding="utf-8")
    monkeypatch.setattr(radar, "PRAZOS_PATH", p)


def test_pending_filtra_e_ordena(tmp_path, monkeypatch):
    today = date.today().isoformat()
    far = (date.today().replace(year=date.today().year + 1)).isoformat()
    _write_prazos(tmp_path, monkeypatch, [
        {"gmail_id": "a", "data_limite": far, "status": "aberto",
         "ato": "x", "resumo": "longe", "processo": ""},
        {"gmail_id": "b", "data_limite": today, "status": "aberto",
         "ato": "y", "resumo": "hoje", "processo": ""},
        {"gmail_id": "c", "data_limite": today, "status": "baixado",
         "ato": "z", "resumo": "fechado", "processo": ""},
    ])
    ps = pending(15)
    assert [p["gmail_id"] for p in ps] == ["b"]     # longe e baixado ficam fora


def test_pending_ultima_linha_vence(tmp_path, monkeypatch):
    today = date.today().isoformat()
    base = {"gmail_id": "a", "data_limite": today, "ato": "x",
            "resumo": "r", "processo": ""}
    _write_prazos(tmp_path, monkeypatch, [
        {**base, "status": "aberto"},
        {**base, "status": "baixado"},   # baixa posterior anula a linha antiga
    ])
    assert pending(15) == []


def test_settle_baixa_unica(tmp_path, monkeypatch):
    today = date.today().isoformat()
    _write_prazos(tmp_path, monkeypatch, [
        {"gmail_id": "a", "data_limite": today, "status": "aberto",
         "ato": "contestação", "resumo": "Contestar na execução fiscal.",
         "processo": "0001234-56.2026.8.16.0017"},
        {"gmail_id": "b", "data_limite": today, "status": "aberto",
         "ato": "embargos", "resumo": "Opor embargos de declaração.",
         "processo": "999"},
    ])
    r = radar.settle("contestacao execucao")     # sem acento: normaliza
    assert "Baixado" in r
    assert [p["gmail_id"] for p in pending(15)] == ["b"]


def test_settle_ambiguo_ou_ausente(tmp_path, monkeypatch):
    today = date.today().isoformat()
    _write_prazos(tmp_path, monkeypatch, [
        {"gmail_id": "a", "data_limite": today, "status": "aberto",
         "ato": "embargos", "resumo": "Embargos no processo 1.", "processo": "1"},
        {"gmail_id": "b", "data_limite": today, "status": "aberto",
         "ato": "embargos", "resumo": "Embargos no processo 2.", "processo": "2"},
    ])
    r = radar.settle("embargos")
    assert "Qual deles" in r and len(pending(15)) == 2   # ambíguo: não baixa
    assert "Nenhum prazo" in radar.settle("apelação")
    assert "Diga qual" in radar.settle("")


def test_speakable(tmp_path, monkeypatch):
    _write_prazos(tmp_path, monkeypatch, [])
    assert "Nenhum prazo" in speakable(15)
    today = date.today().isoformat()
    _write_prazos(tmp_path, monkeypatch, [
        {"gmail_id": "a", "data_limite": today, "status": "aberto",
         "ato": "embargos", "resumo": "Opor embargos.", "processo": "123"}])
    s = speakable(15)
    assert "HOJE" in s and "confirme" in s and "123" in s
