"""Tests for core.llm_client — response parsing and config helpers."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.llm_client import (
    _parse_openai_response,
    _parse_ollama_response,
    _parse_openai_tool_calls,
    _is_model_not_found,
    _sanitize_provider_model,
    _SENT_END,
)


# ── Response parsing ──────────────────────────────────────────────────────

def test_parse_openai_response_basic():
    data = {
        "choices": [{
            "message": {
                "content": "Hello, world!",
                "tool_calls": None,
            }
        }]
    }
    result = _parse_openai_response(data)
    assert result["content"] == "Hello, world!"
    assert result["tool_calls"] == []


def test_parse_openai_response_with_tool_calls():
    data = {
        "choices": [{
            "message": {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_123",
                        "function": {
                            "name": "web_search",
                            "arguments": '{"query": "test"}',
                        },
                    }
                ],
            }
        }]
    }
    result = _parse_openai_response(data)
    assert len(result["tool_calls"]) == 1
    tc = result["tool_calls"][0]
    assert tc["id"] == "call_123"
    assert tc["function"]["name"] == "web_search"
    assert tc["function"]["arguments"] == {"query": "test"}


def test_parse_openai_tool_calls_string_args():
    raw = [
        {
            "id": "call_1",
            "function": {"name": "open_app", "arguments": '{"app_name": "Chrome"}'},
        }
    ]
    result = _parse_openai_tool_calls(raw)
    assert result[0]["function"]["arguments"] == {"app_name": "Chrome"}


def test_parse_openai_tool_calls_dict_args():
    raw = [
        {
            "id": "call_2",
            "function": {"name": "weather_report", "arguments": {"city": "Tokyo"}},
        }
    ]
    result = _parse_openai_tool_calls(raw)
    assert result[0]["function"]["arguments"] == {"city": "Tokyo"}


def test_parse_ollama_response():
    data = {
        "message": {
            "content": "It's sunny today.",
            "tool_calls": [{"function": {"name": "weather_report"}}],
        }
    }
    result = _parse_ollama_response(data)
    assert result["content"] == "It's sunny today."
    assert len(result["tool_calls"]) == 1


def test_parse_ollama_response_empty():
    data = {"message": {"content": ""}}
    result = _parse_ollama_response(data)
    assert result["content"] == ""
    assert result["tool_calls"] == []


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


def test_sanitize_provider_model_for_groq_rejects_ollama_style():
    assert _sanitize_provider_model("gpt-oss:120b-cloud", "groq", fast=False) == "llama-3.3-70b-versatile"
    assert _sanitize_provider_model("gpt-oss:120b-cloud", "groq", fast=True) == "llama-3.1-8b-instant"


def test_sanitize_provider_model_keeps_valid():
    assert _sanitize_provider_model("llama-3.3-70b-versatile", "groq", fast=False) == "llama-3.3-70b-versatile"
