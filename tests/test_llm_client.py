"""Tests for core.llm_client — response parsing and config helpers."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.llm_client import (
    _parse_openai_tool_calls,
    _parse_anthropic_response,
    _is_model_not_found,
    _SENT_END,
    get_llm_provider,
    PROVIDER_CONFIGS,
    _format_anthropic_messages,
    _convert_tools_to_anthropic,
)


# ── Provider configs ──────────────────────────────────────────────────────

def test_providers_exist():
    assert "ollama" in PROVIDER_CONFIGS
    assert "openai" in PROVIDER_CONFIGS
    assert "anthropic" in PROVIDER_CONFIGS
    assert "openrouter" in PROVIDER_CONFIGS


def test_provider_config_structure():
    for name, config in PROVIDER_CONFIGS.items():
        assert "name" in config
        assert "url" in config
        assert "models" in config
        assert "requires_key" in config


# ── Response parsing ──────────────────────────────────────────────────────

def test_parse_anthropic_response_text():
    data = {
        "content": [{"type": "text", "text": "Hello, world!"}],
        "stop_reason": "end_turn",
    }
    result = _parse_anthropic_response(data)
    assert result["content"] == "Hello, world!"
    assert result["tool_calls"] == []


def test_parse_anthropic_response_tool_use():
    data = {
        "content": [
            {"type": "text", "text": "Let me check."},
            {"type": "tool_use", "id": "tc_1", "name": "weather_report", "input": {"city": "Tokyo"}},
        ],
    }
    result = _parse_anthropic_response(data)
    assert result["content"] == "Let me check."
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["function"]["name"] == "weather_report"


def test_parse_openai_tool_calls_string_args():
    raw = [{"id": "call_1", "function": {"name": "open_app", "arguments": '{"app_name": "Chrome"}'}}]
    result = _parse_openai_tool_calls(raw)
    assert result[0]["function"]["arguments"] == {"app_name": "Chrome"}


def test_parse_openai_tool_calls_dict_args():
    raw = [{"id": "call_2", "function": {"name": "weather_report", "arguments": {"city": "Tokyo"}}}]
    result = _parse_openai_tool_calls(raw)
    assert result[0]["function"]["arguments"] == {"city": "Tokyo"}


# ── Anthropic message formatting ──────────────────────────────────────────

def test_format_anthropic_messages():
    messages = [
        {"role": "system", "content": "You are JARVIS."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi!"},
    ]
    system, msgs = _format_anthropic_messages(messages)
    assert system == "You are JARVIS."
    assert len(msgs) == 2
    assert msgs[0] == {"role": "user", "content": "Hello"}
    assert msgs[1] == {"role": "assistant", "content": "Hi!"}


def test_convert_tools_to_anthropic():
    tools = [{
        "type": "function",
        "function": {
            "name": "weather_report",
            "description": "Get weather",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        },
    }]
    result = _convert_tools_to_anthropic(tools)
    assert len(result) == 1
    assert result[0]["name"] == "weather_report"
    assert result[0]["input_schema"]["type"] == "object"


# ── Model not found detection ─────────────────────────────────────────────

def test_is_model_not_found_various():
    assert _is_model_not_found(Exception("model not found")) is True
    assert _is_model_not_found(Exception("pull the model")) is True
    assert _is_model_not_found(Exception("404 error")) is True
    assert _is_model_not_found(Exception("doesn't exist")) is True
    assert _is_model_not_found(Exception("connection refused")) is False


# ── Sentence boundary regex ───────────────────────────────────────────────

def test_sentence_end_regex():
    assert _SENT_END.search("Hello world. Next sentence")
    assert _SENT_END.search("Really? Yes!")
    assert _SENT_END.search("Stop! Now.")
    assert not _SENT_END.search("No punctuation here")
    assert not _SENT_END.search("Version 3.5 is new")
