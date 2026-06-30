"""Tests for agent.planner sanitization of unsupported tools."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.planner import create_plan, replan


def test_create_plan_replaces_unsupported_tool():
    with patch("agent.planner.call_llm_text") as mock_llm:
        mock_llm.return_value = json.dumps(
            {
                "goal": "x",
                "steps": [
                    {
                        "step": 1,
                        "tool": "clipboard",
                        "description": "copy text",
                        "parameters": {"action": "write", "text": "a"},
                    }
                ],
            }
        )
        plan = create_plan("copy something")
    step = plan["steps"][0]
    assert step["tool"] == "web_search"
    assert "query" in step["parameters"]


def test_replan_replaces_generated_code():
    with patch("agent.planner.call_llm_text") as mock_llm:
        mock_llm.return_value = json.dumps(
            {
                "goal": "x",
                "steps": [
                    {
                        "step": 2,
                        "tool": "generated_code",
                        "description": "do thing",
                        "parameters": {},
                    }
                ],
            }
        )
        plan = replan("goal", [], {"tool": "web_search", "description": "x"}, "error")
    step = plan["steps"][0]
    assert step["tool"] == "web_search"
    assert step["parameters"]["query"] == "do thing"
