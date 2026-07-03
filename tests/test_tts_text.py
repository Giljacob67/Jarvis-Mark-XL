"""Tests for core.tts_text — TTS text sanitisation."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.tts_text import sanitize_for_tts


def test_strips_bold_and_italic():
    assert sanitize_for_tts("Isso é **importante** e *urgente*.") == "Isso é importante e urgente."


def test_strips_code_blocks():
    out = sanitize_for_tts("Veja:\n```python\nprint('oi')\n```\nPronto.")
    assert "print" not in out
    assert "```" not in out
    assert "Pronto." in out


def test_strips_inline_code_keeps_content():
    assert sanitize_for_tts("Use o comando `ls -la` agora.") == "Use o comando ls -la agora."


def test_replaces_urls():
    out = sanitize_for_tts("Acesse https://exemplo.com.br/x?q=1 hoje.")
    assert "https" not in out
    assert "link" in out


def test_md_link_keeps_label():
    assert sanitize_for_tts("Veja [o site](https://x.com).") == "Veja o site."


def test_strips_emoji():
    assert sanitize_for_tts("Tudo certo! ✅🚀") == "Tudo certo!"


def test_strips_headings_and_bullets():
    out = sanitize_for_tts("# Título\n- item um\n- item dois")
    assert "#" not in out
    assert "-" not in out.split()[0]


def test_ptbr_currency():
    out = sanitize_for_tts("Custa R$ 1.500,50 no total.", normalize_ptbr=True)
    assert "R$" not in out
    assert "reais" in out


def test_ptbr_acronyms_spelled():
    out = sanitize_for_tts("O CNPJ foi validado pelo STF.", normalize_ptbr=True)
    assert "C N P J" in out
    assert "S T F" in out


def test_no_ptbr_normalization_by_default():
    out = sanitize_for_tts("O CNPJ custa R$ 100.")
    assert "CNPJ" in out
    assert "R$" in out


def test_empty_and_whitespace():
    assert sanitize_for_tts("") == ""
    assert sanitize_for_tts("   \n  ") == ""


def test_plain_text_untouched():
    assert sanitize_for_tts("Bom dia, senhor.") == "Bom dia, senhor."
