"""
MARK XL — LLM response cache.

Caches identical prompts to avoid redundant LLM calls.
Uses a simple in-memory LRU with TTL expiry.

Usage:
    from core.cache import llm_cache
    result = llm_cache.get(key)
    if result is None:
        result = call_llm(...)
        llm_cache.set(key, result)
"""
from __future__ import annotations

import hashlib
import json
import time
import threading
from collections import OrderedDict
from typing import Any

from core.logger import get_logger

log = get_logger("cache")


class TTLCache:
    """Thread-safe in-memory LRU cache with time-to-live expiry."""

    def __init__(self, max_size: int = 128, ttl_seconds: int = 3600):
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            if key in self._cache:
                value, ts = self._cache[key]
                if time.time() - ts < self._ttl:
                    self._cache.move_to_end(key)
                    self._hits += 1
                    return value
                else:
                    del self._cache[key]
            self._misses += 1
            return None

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (value, time.time())
            if len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": f"{self._hits / total * 100:.1f}%" if total else "N/A",
            }


def _make_key(messages: list, model: str = "") -> str:
    """Create a cache key from messages and model."""
    raw = json.dumps({"model": model, "messages": messages}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# Global cache instance
llm_cache = TTLCache(max_size=128, ttl_seconds=1800)  # 30 min TTL


def cached_llm_call(messages: list, model: str = "", **kwargs) -> dict | None:
    """
    Check cache before making an LLM call.
    Returns cached result or None if cache miss.
    """
    key = _make_key(messages, model)
    return llm_cache.get(key)


def cache_llm_result(messages: list, result: dict, model: str = "") -> None:
    """Store an LLM result in cache."""
    key = _make_key(messages, model)
    llm_cache.set(key, result)
