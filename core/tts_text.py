"""
Text sanitisation for TTS — strips markdown/emoji/URLs so the voice never
reads raw formatting, plus optional pt-BR normalisation (currency, acronyms)
for engines without server-side text normalisation (Kokoro).

Single entry point: sanitize_for_tts(text, normalize_ptbr=False)
Applied once, in JarvisLocal.speak(), so every speech path is covered
(streamed sentences, direct tool results, acks, error messages).
"""
from __future__ import annotations

import re
import unicodedata

# Acronyms spoken letter-by-letter in pt-BR (spaced so the engine spells them).
_PTBR_ACRONYMS = {
    "CNPJ", "CPF", "STF", "STJ", "TSE", "TRT", "TST", "INSS", "FGTS",
    "IPTU", "IPVA", "ICMS", "IRPF", "OAB", "SUS", "PIX", "CEP", "CLT",
    "PDF", "URL", "HTML", "USB", "GPS", "CPU", "GPU", "RAM", "SSD",
}

_CODE_BLOCK_RE   = re.compile(r"```.*?(```|$)", re.S)
_INLINE_CODE_RE  = re.compile(r"`([^`]*)`")
_BOLD_ITALIC_RE  = re.compile(r"\*{1,3}([^*\n]+)\*{1,3}")
_UNDERSCORE_RE   = re.compile(r"(?<!\w)_{1,3}([^_\n]+)_{1,3}(?!\w)")
_HEADING_RE      = re.compile(r"^#{1,6}\s*", re.M)
_MD_LINK_RE      = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL_RE          = re.compile(r"(?:https?://|www\.)\S+")
_BULLET_RE       = re.compile(r"^\s*[-•▸*+]\s+", re.M)
_TABLE_ROW_RE    = re.compile(r"^\s*\|.*\|\s*$", re.M)
_LEFTOVER_MD_RE  = re.compile(r"[*#`~|>]")
_WS_RE           = re.compile(r"\s+")

# "R$ 1.500,50" / "R$1500" — move the symbol to the spoken word "reais".
_CURRENCY_RE = re.compile(r"R\$\s*([\d.]+(?:,\d{1,2})?)")


def _strip_emoji(text: str) -> str:
    """Remove emoji and pictographic symbols (Unicode category So/Sk/Cs)."""
    return "".join(
        c for c in text
        if unicodedata.category(c) not in ("So", "Sk", "Cs")
    )


def _spell_acronyms(text: str) -> str:
    """'CNPJ' → 'C N P J' so the engine spells it instead of vocalising it."""
    def _sub(m: re.Match) -> str:
        word = m.group(0)
        return " ".join(word) if word in _PTBR_ACRONYMS else word
    return re.sub(r"\b[A-Z]{2,5}\b", _sub, text)


def _normalize_currency_ptbr(text: str) -> str:
    """'R$ 1.500,50' → '1.500,50 reais' (symbol never reaches the engine)."""
    def _sub(m: re.Match) -> str:
        amount = m.group(1)
        return f"{amount} reais"
    return _CURRENCY_RE.sub(_sub, text)


def _numbers_to_words_ptbr(text: str) -> str:
    """Spell out integers with thousands separators via num2words when available.

    Optional dependency — silently skipped if num2words is not installed
    (digits are still fine for engines with their own normalisation).
    """
    try:
        from num2words import num2words
    except ImportError:
        return text

    def _sub(m: re.Match) -> str:
        raw = m.group(0).replace(".", "")
        try:
            return num2words(int(raw), lang="pt_BR")
        except Exception:
            return m.group(0)

    # Only numbers with thousands separators (1.500) — plain small ints are
    # handled fine by every engine and converting them hurts addresses/IDs.
    return re.sub(r"\b\d{1,3}(?:\.\d{3})+\b", _sub, text)


def sanitize_for_tts(text: str, *, normalize_ptbr: bool = False) -> str:
    """Return `text` safe for speech synthesis.

    Always: strips code blocks, markdown emphasis/headings/links/bullets/
    tables, raw URLs, emojis and leftover formatting characters.

    normalize_ptbr=True (use for engines without text normalisation, e.g.
    Kokoro): additionally converts R$ amounts, spells known acronyms and
    (if num2words is installed) writes out large numbers in pt-BR.
    """
    if not text:
        return ""

    text = _CODE_BLOCK_RE.sub(" código omitido. ", text)
    text = _INLINE_CODE_RE.sub(r"\1", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _URL_RE.sub(" link ", text)
    text = _BOLD_ITALIC_RE.sub(r"\1", text)
    text = _UNDERSCORE_RE.sub(r"\1", text)
    text = _HEADING_RE.sub("", text)
    text = _TABLE_ROW_RE.sub(" ", text)
    text = _BULLET_RE.sub("", text)
    text = _strip_emoji(text)

    if normalize_ptbr:
        text = _normalize_currency_ptbr(text)
        text = _spell_acronyms(text)
        text = _numbers_to_words_ptbr(text)

    text = _LEFTOVER_MD_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()
