"""
MARK XL — Clipboard tool with history.

Actions:
    read          — get current clipboard content
    write <text>  — copy text to clipboard
    clear         — clear clipboard
    history       — show last N clipboard entries
    paste <index> — paste from history by index
"""
from __future__ import annotations

import json
import threading
from collections import deque
from pathlib import Path

from core.paths import BASE_DIR
from core.logger import get_logger

log = get_logger("clipboard")

_HISTORY_PATH = BASE_DIR / "memory" / "clipboard_history.json"
_MAX_HISTORY = 20
_lock = threading.Lock()
_history: deque[str] = deque(maxlen=_MAX_HISTORY)
_loaded = False


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        if _HISTORY_PATH.exists():
            data = json.loads(_HISTORY_PATH.read_text(encoding="utf-8"))
            for item in data[-_MAX_HISTORY:]:
                _history.append(item)
    except Exception:
        pass


def _save_history() -> None:
    try:
        _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _HISTORY_PATH.write_text(
            json.dumps(list(_history), ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        log.warning("Could not save clipboard history: %s", e)


def _add_to_history(text: str) -> None:
    if not text or not text.strip():
        return
    with _lock:
        if not _history or _history[-1] != text:
            _history.append(text)
            _save_history()


def clipboard_tool(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    try:
        import pyperclip
    except ImportError:
        return "pyperclip not installed. Run: pip install pyperclip"

    _ensure_loaded()

    params = parameters or {}
    action = params.get("action", "read").lower()
    text   = params.get("text", "")

    if player:
        player.write_log(f"SYS: Clipboard — {action}")

    if action == "read":
        content = pyperclip.paste()
        if not content:
            return "Clipboard is empty."
        _add_to_history(content)
        return f"Clipboard: {content}"

    elif action == "write":
        if not text:
            return "No text provided to write to clipboard."
        pyperclip.copy(text)
        _add_to_history(text)
        return "Copied to clipboard."

    elif action == "clear":
        pyperclip.copy("")
        return "Clipboard cleared."

    elif action == "history":
        with _lock:
            if not _history:
                return "No clipboard history."
            entries = list(reversed(_history))
        lines = [f"  [{i}] {e[:80]}" for i, e in enumerate(entries)]
        return f"Clipboard history ({len(entries)} entries):\n" + "\n".join(lines)

    elif action == "paste":
        idx = int(params.get("index", 0))
        with _lock:
            if 0 <= idx < len(_history):
                text = _history[idx]
                pyperclip.copy(text)
                return f"Pasted from history [{idx}]: {text[:80]}"
        return f"Invalid history index: {idx}"

    return f"Unknown clipboard action: {action}. Use: read, write, clear, history, paste."
