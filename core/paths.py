"""
Shared base directory resolver for MARK XL.

All modules that need the project root should import from here:
    from core.paths import BASE_DIR
"""
from __future__ import annotations

import sys
from pathlib import Path


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR: Path = _get_base_dir()
CONFIG_DIR: Path = BASE_DIR / "config"
API_CONFIG_PATH: Path = CONFIG_DIR / "api_keys.json"
PROMPT_PATH: Path = BASE_DIR / "core" / "prompt.txt"
