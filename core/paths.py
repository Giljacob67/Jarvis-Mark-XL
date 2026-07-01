"""
Shared base directory resolver for MARK XL.

All modules that need the project root should import from here:
    from core.paths import BASE_DIR
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR: Path = _get_base_dir()


def _resolve_api_config_path() -> Path:
    """
    Resolve api_keys.json with safe fallbacks for worktree setups.

    Priority:
    1. JARVIS_CONFIG_PATH env override
    2. Project-local config/api_keys.json (this checkout)
    3. Main checkout in home dir (~/RepoName/config/api_keys.json)
    4. User-shared config (~/.jarvis/config/api_keys.json)
    5. Project-local default path (if none exists yet)
    """
    env_path = os.environ.get("JARVIS_CONFIG_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()

    project_cfg = BASE_DIR / "config" / "api_keys.json"
    home_repo_cfg = Path.home() / BASE_DIR.parent.name / "config" / "api_keys.json"
    user_shared_cfg = Path.home() / ".jarvis" / "config" / "api_keys.json"

    for candidate in (project_cfg, home_repo_cfg, user_shared_cfg):
        if candidate.exists():
            return candidate
    return project_cfg


API_CONFIG_PATH: Path = _resolve_api_config_path()
CONFIG_DIR: Path = API_CONFIG_PATH.parent
PROMPT_PATH: Path = BASE_DIR / "core" / "prompt.txt"
