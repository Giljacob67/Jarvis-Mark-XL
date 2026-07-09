"""Tests for memory.memory_manager — long-term memory CRUD."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.memory_manager import (
    _empty_memory,
    load_memory,
    update_memory,
    format_memory_for_prompt,
    forget,
    _trim_to_limit,
    MEMORY_MAX_CHARS,
)


# ── Empty memory ──────────────────────────────────────────────────────────

def test_empty_memory_has_all_categories():
    m = _empty_memory()
    expected = {"identity", "preferences", "projects", "relationships", "wishes", "notes"}
    assert set(m.keys()) == expected


def test_empty_memory_values_are_dicts():
    m = _empty_memory()
    for v in m.values():
        assert isinstance(v, dict)


# ── Load / Save cycle ─────────────────────────────────────────────────────

def test_load_memory_returns_dict(tmp_path):
    mem_file = tmp_path / "long_term.json"
    mem_file.write_text(json.dumps(_empty_memory()))
    with patch("memory.memory_manager.MEMORY_PATH", mem_file):
        m = load_memory()
        assert isinstance(m, dict)
        assert "identity" in m


def test_load_memory_missing_file():
    with patch("memory.memory_manager.MEMORY_PATH", Path("/nonexistent/file.json")):
        m = load_memory()
        assert m == _empty_memory()


def test_load_memory_invalid_json(tmp_path):
    mem_file = tmp_path / "long_term.json"
    mem_file.write_text("not valid json {{{")
    with patch("memory.memory_manager.MEMORY_PATH", mem_file):
        m = load_memory()
        assert m == _empty_memory()


# ── Update memory ─────────────────────────────────────────────────────────

def test_update_memory_adds_entry(tmp_path):
    mem_file = tmp_path / "long_term.json"
    mem_file.write_text(json.dumps(_empty_memory()))
    with patch("memory.memory_manager.MEMORY_PATH", mem_file):
        result = update_memory({"identity": {"name": {"value": "Alice"}}})
        assert result["identity"]["name"]["value"] == "Alice"


def test_update_memory_overwrites(tmp_path):
    mem_file = tmp_path / "long_term.json"
    mem_file.write_text(json.dumps(_empty_memory()))
    with patch("memory.memory_manager.MEMORY_PATH", mem_file):
        update_memory({"identity": {"name": {"value": "Alice"}}})
        update_memory({"identity": {"name": {"value": "Bob"}}})
        m = load_memory()
        assert m["identity"]["name"]["value"] == "Bob"


def test_update_memory_empty_noop():
    result = update_memory({})
    assert isinstance(result, dict)


# ── Trim ──────────────────────────────────────────────────────────────────

def test_trim_to_limit_stays_under():
    m = _empty_memory()
    m["notes"]["key"] = {"value": "x" * 100, "updated": "2025-01-01"}
    result = _trim_to_limit(m)
    assert len(json.dumps(result, ensure_ascii=False)) <= MEMORY_MAX_CHARS


def test_trim_does_not_delete_protected():
    m = _empty_memory()
    m["identity"]["name"] = {"value": "important", "updated": "2020-01-01"}
    m["notes"]["junk"] = {"value": "x" * 5000, "updated": "2020-01-01"}
    result = _trim_to_limit(result := m)
    # identity should survive
    assert "name" in result.get("identity", {})


# ── Format for prompt ─────────────────────────────────────────────────────

def test_format_memory_empty():
    assert format_memory_for_prompt(None) == ""
    assert format_memory_for_prompt({}) == ""


def test_format_memory_with_identity():
    m = _empty_memory()
    m["identity"]["name"] = {"value": "Alice"}
    result = format_memory_for_prompt(m)
    assert "Alice" in result
    assert "Name" in result or "name" in result.lower()


def test_format_memory_truncates():
    m = _empty_memory()
    m["notes"]["big"] = {"value": "x" * 3000}
    result = format_memory_for_prompt(m)
    assert len(result) <= 2000


# ── Forget ────────────────────────────────────────────────────────────────

def test_forget_existing(tmp_path):
    mem_file = tmp_path / "long_term.json"
    mem_file.write_text(json.dumps(_empty_memory()))
    with patch("memory.memory_manager.MEMORY_PATH", mem_file):
        update_memory({"notes": {"test_key": {"value": "test_val"}}})
        result = forget("test_key", "notes")
        assert "Forgotten" in result


def test_forget_nonexistent():
    result = forget("nonexistent_key", "notes")
    assert "Not found" in result


# ── Cross-session recall ──────────────────────────────────────────────────

def test_get_recent_context_excludes_current_and_bounds(tmp_path):
    db = tmp_path / "conversations.db"
    with patch("memory.conversation_db._DB_PATH", db):
        from memory import conversation_db as cdb

        cdb.init_db()
        # Previous conversation with content
        old = cdb.create_conversation("old")
        cdb.add_message(old, "user", "Qual o prazo da ação X?")
        cdb.add_message(old, "assistant", "O prazo é 15/08.")
        # Current (empty) conversation must be excluded
        cur = cdb.create_conversation("cur")

        ctx = cdb.get_recent_context(exclude_conv_id=cur)
        assert "15/08" in ctx
        assert len(ctx) <= 1400

        # With nothing but the current conv, recall is empty
        empty = cdb.get_recent_context(exclude_conv_id=old)
        assert empty == ""

