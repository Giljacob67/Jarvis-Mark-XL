"""Tests for DeepgramLiveSTT message assembly (segments → utterances)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.stt_deepgram import DeepgramLiveSTT


def _make():
    d = DeepgramLiveSTT.__new__(DeepgramLiveSTT)
    d._segments = []
    d._utterance_start = None
    finals, interims = [], []
    d._on_final   = lambda text, ms: finals.append(text)
    d._on_interim = lambda text: interims.append(text)
    return d, finals, interims


def _results(text, is_final=False, speech_final=False, **extra):
    return json.dumps({
        "type": "Results", "is_final": is_final, "speech_final": speech_final,
        "channel": {"alternatives": [{"transcript": text}]}, **extra,
    })


def test_mid_speech_segment_is_not_lost():
    """Bug real: is_final sem speech_final descartava o comando inteiro."""
    d, finals, _ = _make()
    d._handle_message(_results("Jarvis, verifique meus emails.", is_final=True))
    assert finals == []                      # segmento guardado, não perdido
    d._handle_message(_results("por favor", is_final=True, speech_final=True))
    assert finals == ["Jarvis, verifique meus emails. por favor"]


def test_empty_speech_final_still_flushes():
    """speech_final pode vir com transcript vazio — precisa entregar mesmo assim."""
    d, finals, _ = _make()
    d._handle_message(_results("abra o navegador", is_final=True))
    d._handle_message(_results("", is_final=True, speech_final=True))
    assert finals == ["abra o navegador"]


def test_utterance_end_flushes_pending():
    """Ruído de fundo pode impedir o speech_final; UtteranceEnd cobre o caso."""
    d, finals, _ = _make()
    d._handle_message(_results("que horas são", is_final=True))
    d._handle_message(json.dumps({"type": "UtteranceEnd"}))
    assert finals == ["que horas são"]
    d._handle_message(json.dumps({"type": "UtteranceEnd"}))
    assert finals == ["que horas são"]       # sem pendência → sem duplicata


def test_interim_shows_accumulated_sentence():
    d, _, interims = _make()
    d._handle_message(_results("Jarvis, verifique", is_final=True))
    d._handle_message(_results("meus emails"))
    assert interims[-1] == "Jarvis, verifique meus emails"


def test_from_finalize_flushes():
    """Resposta do frame Finalize (gate fechou) também entrega os segmentos."""
    d, finals, _ = _make()
    d._handle_message(_results("texto preso", is_final=True))
    d._handle_message(_results("", is_final=True, from_finalize=True))
    assert finals == ["texto preso"]


def test_reset_clears_segments():
    d, finals, _ = _make()
    d._handle_message(_results("lixo antigo", is_final=True))
    d.reset_utterance()
    d._handle_message(_results("comando novo", is_final=True, speech_final=True))
    assert finals == ["comando novo"]


def test_garbage_json_ignored():
    d, finals, _ = _make()
    d._handle_message("{not json")
    d._handle_message(json.dumps({"type": "Metadata"}))
    assert finals == []
