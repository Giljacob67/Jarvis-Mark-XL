"""Tests for core.security — code execution gate."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, mock_open

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.security import code_execution_allowed, CODE_EXEC_DISABLED_MSG


def test_code_execution_disabled_by_default():
    """Without config, code execution should be denied."""
    with patch("core.security._CONFIG_PATH") as mock_path:
        mock_path.read_text.side_effect = FileNotFoundError
        assert code_execution_allowed() is False


def test_code_execution_denied_when_key_missing():
    """When allow_code_execution is absent, should return False."""
    with patch("core.security._CONFIG_PATH") as mock_path:
        mock_path.read_text.return_value = json.dumps({})
        assert code_execution_allowed() is False


def test_code_execution_denied_when_false():
    with patch("core.security._CONFIG_PATH") as mock_path:
        mock_path.read_text.return_value = json.dumps({"allow_code_execution": False})
        assert code_execution_allowed() is False


def test_code_execution_allowed_when_true():
    with patch("core.security._CONFIG_PATH") as mock_path:
        mock_path.read_text.return_value = json.dumps({"allow_code_execution": True})
        assert code_execution_allowed() is True


def test_disabled_msg_is_string():
    assert isinstance(CODE_EXEC_DISABLED_MSG, str)
    assert "disabled" in CODE_EXEC_DISABLED_MSG.lower()
