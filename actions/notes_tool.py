"""
MARK XL — Notes tool.

Simple local note-taking system stored in JSON.

Actions:
    add <text>     — add a new note
    list [limit]   — list recent notes
    search <query> — search notes
    delete <id>    — delete a note by ID
    clear          — clear all notes
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from core.paths import BASE_DIR
from core.logger import get_logger

log = get_logger("notes")

_NOTES_PATH = BASE_DIR / "memory" / "notes.json"


def _load_notes() -> list[dict]:
    try:
        if _NOTES_PATH.exists():
            return json.loads(_NOTES_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _save_notes(notes: list[dict]) -> None:
    _NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _NOTES_PATH.write_text(
        json.dumps(notes, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def notes_tool(
    parameters: dict,
    player=None,
    speak=None,
) -> str:
    params = parameters or {}
    action = params.get("action", "add").lower()
    text = params.get("text", "")
    query = params.get("query", "")
    limit = int(params.get("limit", 10))

    if player:
        player.write_log(f"SYS: Notes — {action}")

    notes = _load_notes()

    if action == "add":
        if not text:
            return "No text provided for the note."
        note = {
            "id": len(notes) + 1,
            "text": text,
            "created": time.strftime("%Y-%m-%d %H:%M"),
        }
        notes.append(note)
        _save_notes(notes)
        return f"Note #{note['id']} saved."

    elif action == "list":
        if not notes:
            return "No notes yet."
        recent = notes[-limit:]
        lines = [f"  [{n['id']}] {n['text'][:60]} ({n['created']})" for n in recent]
        return f"Notes ({len(notes)} total):\n" + "\n".join(lines)

    elif action == "search":
        if not query:
            return "Provide a search query."
        matches = [n for n in notes if query.lower() in n["text"].lower()]
        if not matches:
            return f"No notes matching '{query}'."
        lines = [f"  [{n['id']}] {n['text'][:60]}" for n in matches[-limit:]]
        return f"Found {len(matches)} notes:\n" + "\n".join(lines)

    elif action == "delete":
        note_id = int(params.get("id", 0))
        original_len = len(notes)
        notes = [n for n in notes if n["id"] != note_id]
        if len(notes) == original_len:
            return f"Note #{note_id} not found."
        _save_notes(notes)
        return f"Note #{note_id} deleted."

    elif action == "clear":
        _save_notes([])
        return "All notes cleared."

    return f"Unknown notes action: {action}"
