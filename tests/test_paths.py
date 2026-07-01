"""Tests for core.paths — shared base directory resolver."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.paths import BASE_DIR, API_CONFIG_PATH, PROMPT_PATH, CONFIG_DIR


def test_base_dir_is_project_root():
    assert BASE_DIR.is_dir()
    assert (BASE_DIR / "main.py").exists()


def test_api_config_path_points_to_config():
    if os.environ.get("JARVIS_CONFIG_PATH"):
        assert API_CONFIG_PATH == Path(os.environ["JARVIS_CONFIG_PATH"]).expanduser()
    else:
        assert API_CONFIG_PATH.name == "api_keys.json"
        assert API_CONFIG_PATH.parent == CONFIG_DIR


def test_prompt_path_points_to_prompt():
    assert PROMPT_PATH == BASE_DIR / "core" / "prompt.txt"


def test_config_dir_exists():
    assert CONFIG_DIR.name == "config"
