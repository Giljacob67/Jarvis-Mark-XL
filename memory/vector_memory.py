"""
Vector Memory for JARVIS using AgentDB.

Provides semantic search over long-term memories, conversation history,
and learned patterns. Replaces simple JSON lookup with HNSW-indexed
vector similarity search.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from core.logger import get_logger
from core.paths import BASE_DIR

log = get_logger("vector_memory")

VECTOR_DB_PATH = BASE_DIR / "memory" / "vector_db"
VECTOR_DB_PATH.mkdir(parents=True, exist_ok=True)

_agentdb = None
_initialized = False


def _get_agentdb():
    """Lazy-load AgentDB instance."""
    global _agentdb, _initialized
    if _initialized:
        return _agentdb
    
    try:
        from agentdb import AgentDB
        _agentdb = AgentDB(str(VECTOR_DB_PATH))
        _initialized = True
        log.info("AgentDB initialized at %s", VECTOR_DB_PATH)
    except ImportError:
        log.warning("AgentDB not installed. Vector memory disabled.")
        _initialized = True
    except Exception as e:
        log.error("AgentDB init failed: %s", e)
        _initialized = True
    
    return _agentdb


def is_available() -> bool:
    """Check if vector memory is available."""
    return _get_agentdb() is not None


def store_memory(
    content: str,
    category: str,
    key: str,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Store a memory entry with vector embedding."""
    db = _get_agentdb()
    if not db:
        return False
    
    try:
        meta = {
            "category": category,
            "key": key,
            "timestamp": datetime.now().isoformat(),
            **(metadata or {}),
        }
        db.upsert(key=f"{category}/{key}", content=content, metadata=meta)
        log.debug("Stored vector memory: %s/%s", category, key)
        return True
    except Exception as e:
        log.error("Failed to store vector memory: %s", e)
        return False


def search_memory(
    query: str,
    category: str | None = None,
    limit: int = 10,
    min_score: float = 0.3,
) -> list[dict[str, Any]]:
    """Semantic search over stored memories."""
    db = _get_agentdb()
    if not db:
        return []
    
    try:
        results = db.search(
            query=query,
            limit=limit,
            filter_metadata={"category": category} if category else None,
        )
        
        filtered = [
            {
                "key": r.metadata.get("key", ""),
                "category": r.metadata.get("category", ""),
                "content": r.content,
                "score": r.score,
                "metadata": r.metadata,
            }
            for r in results
            if r.score >= min_score
        ]
        return filtered
    except Exception as e:
        log.error("Vector search failed: %s", e)
        return []


def get_memory(key: str, category: str) -> dict[str, Any] | None:
    """Retrieve a specific memory by key."""
    db = _get_agentdb()
    if not db:
        return None
    
    try:
        result = db.get(key=f"{category}/{key}")
        if result:
            return {
                "key": result.metadata.get("key", ""),
                "category": result.metadata.get("category", ""),
                "content": result.content,
                "metadata": result.metadata,
            }
    except Exception as e:
        log.error("Failed to get vector memory: %s", e)
    return None


def delete_memory(key: str, category: str) -> bool:
    """Delete a memory entry."""
    db = _get_agentdb()
    if not db:
        return False
    
    try:
        db.delete(key=f"{category}/{key}")
        return True
    except Exception as e:
        log.error("Failed to delete vector memory: %s", e)
        return False


def sync_from_json(json_path: Path) -> int:
    """One-time migration from JSON memory to vector DB."""
    if not json_path.exists():
        return 0
    
    db = _get_agentdb()
    if not db:
        return 0
    
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        count = 0
        for category, entries in data.items():
            if not isinstance(entries, dict):
                continue
            for key, entry in entries.items():
                if isinstance(entry, dict) and "value" in entry:
                    content = entry["value"]
                    meta = {k: v for k, v in entry.items() if k != "value"}
                    store_memory(content, category, key, meta)
                    count += 1
        log.info("Migrated %d memories from JSON to vector DB", count)
        return count
    except Exception as e:
        log.error("Migration failed: %s", e)
        return 0


def get_stats() -> dict[str, Any]:
    """Get vector DB statistics."""
    db = _get_agentdb()
    if not db:
        return {"available": False}
    
    try:
        stats = db.stats()
        return {
            "available": True,
            "total_vectors": stats.get("total_vectors", 0),
            "index_size_mb": stats.get("index_size_mb", 0),
            "dimensions": stats.get("dimensions", 0),
        }
    except Exception as e:
        log.error("Stats failed: %s", e)
        return {"available": True, "error": str(e)}


class VectorMemoryMixin:
    """Mixin to add vector search to existing memory manager."""
    
    def vector_search(self, query: str, limit: int = 5) -> str:
        """Search memories semantically and format for prompt."""
        results = search_memory(query, limit=limit)
        if not results:
            return ""
        
        lines = ["[RELEVANT MEMORIES FROM VECTOR SEARCH]"]
        for r in results:
            lines.append(f"  - [{r['category']}/{r['key']}] (score: {r['score']:.2f}) {r['content'][:200]}")
        return "\n".join(lines) + "\n"