"""Tests for agent modules — planner, error_handler."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.error_handler import analyze_error, generate_fix, ErrorDecision


# ── Error handler tests ───────────────────────────────────────────────────

def test_analyze_error_max_attempts_forces_replan():
    """After max attempts, should force replan regardless of LLM response."""
    step = {"step": 1, "tool": "web_search", "description": "Search", "critical": False}
    result = analyze_error(step, "timeout", attempt=3, max_attempts=2)
    assert result["decision"] == ErrorDecision.REPLAN


def test_analyze_error_returns_decision_enum():
    """Decision should be an ErrorDecision enum value."""
    step = {"step": 1, "tool": "web_search", "description": "Search", "critical": False}
    with patch("agent.error_handler.call_llm_text") as mock_llm:
        mock_llm.return_value = json.dumps({
            "decision": "retry",
            "reason": "transient",
            "fix_suggestion": "",
            "max_retries": 1,
            "user_message": "Retrying",
        })
        result = analyze_error(step, "network error", attempt=1)
        assert isinstance(result["decision"], ErrorDecision)


def test_analyze_error_critical_step_cannot_skip():
    """Critical steps should not be skipped — replan instead."""
    step = {"step": 1, "tool": "web_search", "description": "Search", "critical": True}
    with patch("agent.error_handler.call_llm_text") as mock_llm:
        mock_llm.return_value = json.dumps({
            "decision": "skip",
            "reason": "not important",
            "fix_suggestion": "",
            "max_retries": 0,
            "user_message": "Skipping",
        })
        result = analyze_error(step, "error", attempt=1)
        assert result["decision"] == ErrorDecision.REPLAN


def test_analyze_error_llm_failure_defaults_to_replan():
    """If LLM analysis fails, should default to replan."""
    step = {"step": 1, "tool": "web_search", "description": "Search", "critical": False}
    with patch("agent.error_handler.call_llm_text") as mock_llm:
        mock_llm.side_effect = Exception("LLM down")
        result = analyze_error(step, "error", attempt=1)
        assert result["decision"] == ErrorDecision.REPLAN
