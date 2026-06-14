from __future__ import annotations

import json

from core.paths import BASE_DIR, CONFIG_DIR
CONFIG_FILE = CONFIG_DIR / "api_keys.json"


def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def config_exists() -> bool:
    return CONFIG_FILE.exists()


def save_config(cfg: dict) -> None:
    ensure_config_dir()
    existing: dict = {}
    if CONFIG_FILE.exists():
        try:
            existing = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    existing.update(cfg)
    CONFIG_FILE.write_text(json.dumps(existing, indent=2), encoding="utf-8")


def load_api_keys() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ Failed to load api_keys.json: {e}")
        return {}


def is_configured() -> bool:
    cfg = load_api_keys()
    return (
        bool(cfg.get("os_system")) and
        bool(cfg.get("llm_model")) and
        bool(cfg.get("stt_engine")) and
        bool(cfg.get("tts_engine"))
    )
