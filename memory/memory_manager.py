from __future__ import annotations

import json
import shutil
from datetime import date, datetime
from threading import Lock

from core.logger import get_logger
from core.paths import BASE_DIR

log = get_logger("memory")

MEMORY_PATH = BASE_DIR / "memory" / "long_term.json"
_lock            = Lock()
MAX_VALUE_LENGTH = 600
MEMORY_MAX_CHARS = 6000
MEMORY_BACKUP_MAX = 7   # keep last 7 daily backups

# Try to import vector memory
try:
    from memory.vector_memory import (
        store_memory as _vector_store,
        search_memory as _vector_search,
        sync_from_json as _vector_sync,
        is_available as _vector_available,
    )
    _VECTOR_ENABLED = True
except ImportError:
    _VECTOR_ENABLED = False

# Categories ordered by deletion priority (higher = deleted first when trimming)
_CATEGORY_WEIGHT = {
    "identity":      0,   # never deleted
    "preferences":   1,
    "relationships": 2,
    "projects":      3,
    "wishes":        4,
    "notes":         5,   # deleted first
}
_PROTECTED_CATEGORIES = {"identity"}

def _empty_memory() -> dict:
    return {
        "identity":      {},
        "preferences":   {},
        "projects":      {},
        "relationships": {},
        "wishes":        {},
        "notes":         {},
    }

def load_memory() -> dict:
    if not MEMORY_PATH.exists():
        return _empty_memory()
    with _lock:
        try:
            data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                base = _empty_memory()
                for key in base:
                    if key not in data:
                        data[key] = {}
                return data
            return _empty_memory()
        except Exception as e:
            log.warning("Load error: %s", e)
            return _empty_memory()

def _all_entries(memory: dict) -> list[tuple]:
    entries = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            if isinstance(entry, dict) and "value" in entry:
                entries.append((cat, key, entry))
    return entries


def _trim_score(cat: str, entry: dict) -> float:
    """Higher score = deleted first. Protected categories always score 0."""
    if cat in _PROTECTED_CATEGORIES:
        return 0.0
    cat_weight = _CATEGORY_WEIGHT.get(cat, 5)
    updated_str = entry.get("updated", "2000-01-01")
    try:
        days_old = (date.today() - date.fromisoformat(updated_str)).days
    except ValueError:
        days_old = 0
    return days_old * 0.6 + cat_weight * 0.4


def _trim_to_limit(memory: dict) -> dict:
    if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
        return memory
    entries = _all_entries(memory)
    # Sort by deletion priority: highest score deleted first; protected items last
    entries.sort(key=lambda t: -_trim_score(t[0], t[2]))
    for cat, key, _ in entries:
        if cat in _PROTECTED_CATEGORIES:
            continue
        if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
            break
        del memory[cat][key]
        log.info("Trimmed %s/%s", cat, key)
    return memory

def _rotate_backups() -> None:
    """Keep at most MEMORY_BACKUP_MAX daily backup files."""
    today = date.today().isoformat()
    backup = MEMORY_PATH.parent / f"long_term_{today}.json"
    if MEMORY_PATH.exists() and not backup.exists():
        try:
            shutil.copy2(MEMORY_PATH, backup)
        except Exception as e:
            log.warning("Backup failed: %s", e)
    # prune old backups
    backups = sorted(MEMORY_PATH.parent.glob("long_term_*.json"))
    for old in backups[:-MEMORY_BACKUP_MAX]:
        try:
            old.unlink()
        except Exception:
            pass


def save_memory(memory: dict) -> None:
    if not isinstance(memory, dict):
        return
    memory = _trim_to_limit(memory)
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _rotate_backups()
    with _lock:
        MEMORY_PATH.write_text(
            json.dumps(memory, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _truncate_value(val: str) -> str:
    if isinstance(val, str) and len(val) > MAX_VALUE_LENGTH:
        return val[:MAX_VALUE_LENGTH].rstrip() + "…"
    return val


def _recursive_update(target: dict, updates: dict) -> bool:
    changed = False
    for key, value in updates.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, dict) and "value" not in value:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
                changed = True
            if _recursive_update(target[key], value):
                changed = True
        else:
            new_val  = _truncate_value(str(value["value"] if isinstance(value, dict) else value))
            entry    = {"value": new_val, "updated": datetime.now().strftime("%Y-%m-%d")}
            existing = target.get(key, {})
            if not isinstance(existing, dict) or existing.get("value") != new_val:
                target[key] = entry
                changed = True
    return changed


def update_memory(memory_update: dict) -> dict:
    if not isinstance(memory_update, dict) or not memory_update:
        return load_memory()
    memory = load_memory()
    if _recursive_update(memory, memory_update):
        save_memory(memory)
        log.info("Saved: %s", list(memory_update.keys()))
        
        # Also store in vector DB for semantic search
        if _VECTOR_ENABLED:
            for category, entries in memory_update.items():
                if isinstance(entries, dict):
                    for key, entry in entries.items():
                        if isinstance(entry, dict) and "value" in entry:
                            _vector_store(
                                content=entry["value"],
                                category=category,
                                key=key,
                                metadata={k: v for k, v in entry.items() if k != "value"},
                            )
    return memory

def format_memory_for_prompt(memory: dict | None) -> str:
    if not memory:
        return ""

    lines = []

    identity  = memory.get("identity", {})
    id_fields = ["name", "age", "birthday", "city", "job", "language", "school", "nationality"]
    for field in id_fields:
        entry = identity.get(field)
        if entry:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"{field.title()}: {val}")
    for key, entry in identity.items():
        if key in id_fields:
            continue
        val = entry.get("value") if isinstance(entry, dict) else entry
        if val:
            lines.append(f"{key.replace('_', ' ').title()}: {val}")

    prefs = memory.get("preferences", {})
    if prefs:
        lines.append("")
        lines.append("Preferences:")
        for key, entry in list(prefs.items())[:15]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    projects = memory.get("projects", {})
    if projects:
        lines.append("")
        lines.append("Active Projects / Goals:")
        for key, entry in list(projects.items())[:8]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    rels = memory.get("relationships", {})
    if rels:
        lines.append("")
        lines.append("People in their life:")
        for key, entry in list(rels.items())[:10]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    wishes = memory.get("wishes", {})
    if wishes:
        lines.append("")
        lines.append("Wishes / Plans / Wants:")
        for key, entry in list(wishes.items())[:8]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    notes = memory.get("notes", {})
    if notes:
        lines.append("")
        lines.append("Other notes:")
        for key, entry in list(notes.items())[:8]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key}: {val}")

    if not lines:
        return ""

    header = "[WHAT YOU KNOW ABOUT THIS PERSON — use naturally, never recite like a list]\n"
    result = header + "\n".join(lines)
    if len(result) > 2000:
        result = result[:1997] + "…"

    return result + "\n"


def format_vector_memory_for_prompt(query: str, limit: int = 5) -> str:
    """Get semantically relevant memories for the current query."""
    if not _VECTOR_ENABLED:
        return ""
    try:
        results = _vector_search(query, limit=limit)
        if not results:
            return ""
        lines = ["[SEMANTICALLY RELEVANT MEMORIES]"]
        for r in results:
            lines.append(f"  - [{r['category']}/{r['key']}] (relevance: {r['score']:.2f}) {r['content'][:200]}")
        return "\n".join(lines) + "\n"
    except Exception as e:
        log.warning("Vector memory search failed: %s", e)
        return ""


def migrate_to_vector_db() -> int:
    """One-time migration from JSON to vector DB."""
    if not _VECTOR_ENABLED:
        return 0
    return _vector_sync(MEMORY_PATH)


def remember(key: str, value: str, category: str = "notes") -> str:
    valid = {"identity", "preferences", "projects", "relationships", "wishes", "notes"}
    if category not in valid:
        category = "notes"
    update_memory({category: {key: {"value": value}}})
    return f"Remembered: {category}/{key} = {value}"


def forget(key: str, category: str = "notes") -> str:
    memory = load_memory()
    cat    = memory.get(category, {})
    if key in cat:
        del cat[key]
        memory[category] = cat
        save_memory(memory)
        return f"Forgotten: {category}/{key}"
    return f"Not found: {category}/{key}"


forget_memory = forget
