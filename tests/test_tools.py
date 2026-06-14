"""Tests for core.tools — tool declarations and Ollama format conversion."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.tools import TOOL_DECLARATIONS, OLLAMA_TOOLS, TOOL_NAMES, _to_ollama_tools


def test_tool_declarations_count():
    assert len(TOOL_DECLARATIONS) == 20


def test_all_tools_have_name():
    for t in TOOL_DECLARATIONS:
        assert "name" in t
        assert isinstance(t["name"], str)
        assert len(t["name"]) > 0


def test_all_tools_have_description():
    for t in TOOL_DECLARATIONS:
        assert "description" in t
        assert len(t["description"]) > 0


def test_all_tools_have_parameters():
    for t in TOOL_DECLARATIONS:
        assert "parameters" in t
        assert t["parameters"]["type"] == "OBJECT"


def test_ollama_tools_format():
    assert len(OLLAMA_TOOLS) == len(TOOL_DECLARATIONS)
    for tool in OLLAMA_TOOLS:
        assert tool["type"] == "function"
        assert "function" in tool
        assert "name" in tool["function"]
        assert "description" in tool["function"]
        assert "parameters" in tool["function"]
        params = tool["function"]["parameters"]
        assert params["type"] == "object"  # lowercase in Ollama format
        assert "properties" in params


def test_ollama_type_conversion():
    """Gemini-style types (STRING, INTEGER, etc.) should be lowercase in Ollama format."""
    decl = [{
        "name": "test_tool",
        "description": "Test",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING", "description": "Name"},
                "count": {"type": "INTEGER", "description": "Count"},
                "flag": {"type": "BOOLEAN", "description": "Flag"},
            },
            "required": ["name"],
        },
    }]
    result = _to_ollama_tools(decl)
    props = result[0]["function"]["parameters"]["properties"]
    assert props["name"]["type"] == "string"
    assert props["count"]["type"] == "integer"
    assert props["flag"]["type"] == "boolean"
    assert result[0]["function"]["parameters"]["required"] == ["name"]


def test_tool_names_matches_declarations():
    assert TOOL_NAMES == [d["name"] for d in TOOL_DECLARATIONS]


def test_wake_word_tool_exists():
    names = {d["name"] for d in TOOL_DECLARATIONS}
    assert "save_memory" in names
    assert "open_app" in names
    assert "browser_control" in names
    assert "shutdown_jarvis" in names
