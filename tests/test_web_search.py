"""Tests for actions.web_search provider selection and fallback."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.web_search import web_search


def test_web_search_auto_uses_brave_when_key_present():
    with patch("actions.web_search._load_search_config", return_value={"brave_api_key": "k"}):
        with patch("actions.web_search._brave_search", return_value=[{"title": "t", "snippet": "s", "url": "u"}]) as brave:
            with patch("actions.web_search._llm_summarize", return_value="ok"):
                result = web_search({"query": "jarvis"})
    assert result == "ok"
    brave.assert_called_once()


def test_web_search_auto_uses_ddg_without_brave_key():
    with patch("actions.web_search._load_search_config", return_value={}):
        with patch("actions.web_search._ddg_search", return_value=[{"title": "t", "snippet": "s", "url": "u"}]) as ddg:
            with patch("actions.web_search._llm_summarize", return_value="ok"):
                result = web_search({"query": "jarvis"})
    assert result == "ok"
    ddg.assert_called_once()


def test_web_search_brave_falls_back_to_ddg_on_error():
    with patch("actions.web_search._load_search_config", return_value={"brave_api_key": "k"}):
        with patch("actions.web_search._brave_search", side_effect=RuntimeError("fail")):
            with patch("actions.web_search._ddg_search", return_value=[{"title": "t", "snippet": "s", "url": "u"}]) as ddg:
                with patch("actions.web_search._llm_summarize", return_value="ok"):
                    result = web_search({"query": "jarvis", "provider": "brave"})
    assert result == "ok"
    ddg.assert_called_once()
