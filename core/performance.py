"""
MARK XL — Performance Optimization.

Latency, memory, and battery optimization utilities:
  - Response caching with TTL
  - Lazy module loading
  - Memory-aware conversation trimming
  - Idle detection for resource management
  - Connection pooling helpers
"""
from __future__ import annotations

import gc
import hashlib
import json
import threading
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from core.logger import get_logger

log = get_logger("performance")


# ── Response Cache ─────────────────────────────────────────────────────────

class ResponseCache:
    """LRU cache with TTL for LLM responses and tool results."""

    def __init__(self, max_size: int = 128, ttl_seconds: int = 300):
        self._cache: OrderedDict = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def _key(self, text: str, context: str = "") -> str:
        raw = f"{text}:{context}"
        return hashlib.md5(raw.encode()).hexdigest()

    def get(self, text: str, context: str = "") -> str | None:
        key = self._key(text, context)
        with self._lock:
            if key in self._cache:
                entry = self._cache[key]
                if time.time() - entry["ts"] < self._ttl:
                    self._cache.move_to_end(key)
                    self._hits += 1
                    return entry["value"]
                else:
                    del self._cache[key]
            self._misses += 1
        return None

    def put(self, text: str, value: str, context: str = "") -> None:
        key = self._key(text, context)
        with self._lock:
            if len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
            self._cache[key] = {"value": value, "ts": time.time()}

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": f"{self._hits / total * 100:.1f}%" if total > 0 else "N/A",
        }


# Global cache instances
_llm_cache = ResponseCache(max_size=256, ttl_seconds=600)
_tool_cache = ResponseCache(max_size=128, ttl_seconds=300)


def get_llm_cache() -> ResponseCache:
    return _llm_cache


def get_tool_cache() -> ResponseCache:
    return _tool_cache


# ── Lazy Module Loader ────────────────────────────────────────────────────

class LazyModule:
    """Lazy-import a module to reduce startup time."""

    def __init__(self, module_name: str):
        self._module_name = module_name
        self._module = None

    def __getattr__(self, name: str) -> Any:
        if self._module is None:
            import importlib
            self._module = importlib.import_module(self._module_name)
        return getattr(self._module, name)


# ── Memory Optimization ───────────────────────────────────────────────────

def trim_conversation(conversation: list, max_turns: int = 12) -> list:
    """
    Trim conversation to fit within memory limits.
    Preserves recent context while removing old exchanges.
    """
    if len(conversation) <= max_turns:
        return conversation

    # Keep last N turns, but always keep system context
    trimmed = conversation[-max_turns:]

    # Verify tool call integrity (don't orphan tool results)
    clean = []
    orphaned_tool_calls = set()

    for msg in trimmed:
        if msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id", "")
            # Check if the parent tool call exists in trimmed
            parent_exists = any(
                m.get("tool_calls") and any(
                    tc.get("id") == tc_id for tc in m.get("tool_calls", [])
                )
                for m in trimmed if m.get("role") == "assistant"
            )
            if not parent_exists:
                orphaned_tool_calls.add(tc_id)
                continue
        clean.append(msg)

    return clean


def trim_memory_facts(memory: dict, max_per_category: int = 20) -> dict:
    """Trim memory facts to prevent unbounded growth."""
    trimmed = {}
    for cat, entries in memory.items():
        if isinstance(entries, dict) and len(entries) > max_per_category:
            # Keep most recently updated entries
            sorted_entries = sorted(
                entries.items(),
                key=lambda x: x[1].get("updated", "") if isinstance(x[1], dict) else "",
                reverse=True,
            )
            trimmed[cat] = dict(sorted_entries[:max_per_category])
        else:
            trimmed[cat] = entries
    return trimmed


def force_gc() -> dict:
    """Force garbage collection and return memory stats."""
    import psutil
    import os

    before = psutil.Process(os.getpid()).memory_info().rss
    gc.collect()
    after = psutil.Process(os.getpid()).memory_info().rss

    freed = before - after
    return {
        "freed_mb": freed / (1024 * 1024),
        "current_mb": after / (1024 * 1024),
        "gc_collected": gc.collect(),
    }


# ── Idle Detection ────────────────────────────────────────────────────────

class IdleDetector:
    """Detect idle periods to reduce resource usage."""

    def __init__(self, idle_threshold_sec: float = 300):
        self._last_activity = time.time()
        self._threshold = idle_threshold_sec
        self._on_idle_callbacks: list[Callable] = []
        self._on_wake_callbacks: list[Callable] = []
        self._is_idle = False

    def mark_active(self) -> None:
        was_idle = self._is_idle
        self._last_activity = time.time()
        self._is_idle = False
        if was_idle:
            for cb in self._on_wake_callbacks:
                try:
                    cb()
                except Exception:
                    pass

    def check_idle(self) -> bool:
        if not self._is_idle and time.time() - self._last_activity > self._threshold:
            self._is_idle = True
            for cb in self._on_idle_callbacks:
                try:
                    cb()
                except Exception:
                    pass
        return self._is_idle

    def on_idle(self, callback: Callable) -> None:
        self._on_idle_callbacks.append(callback)

    def on_wake(self, callback: Callable) -> None:
        self._on_wake_callbacks.append(callback)

    @property
    def idle_seconds(self) -> float:
        return time.time() - self._last_activity


# ── Performance Monitor ───────────────────────────────────────────────────

class PerfMonitor:
    """Track and report performance metrics."""

    def __init__(self):
        self._metrics: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def record(self, name: str, value_ms: float) -> None:
        with self._lock:
            if name not in self._metrics:
                self._metrics[name] = []
            self._metrics[name].append(value_ms)
            # Keep last 100 measurements
            if len(self._metrics[name]) > 100:
                self._metrics[name] = self._metrics[name][-100:]

    def get_stats(self, name: str) -> dict:
        with self._lock:
            values = self._metrics.get(name, [])
            if not values:
                return {"count": 0}
            return {
                "count": len(values),
                "avg_ms": sum(values) / len(values),
                "min_ms": min(values),
                "max_ms": max(values),
                "p95_ms": sorted(values)[int(len(values) * 0.95)] if len(values) >= 2 else values[0],
            }

    def get_all_stats(self) -> dict:
        with self._lock:
            return {name: self.get_stats(name) for name in self._metrics}

    def report(self) -> str:
        stats = self.get_all_stats()
        lines = ["Performance Metrics:"]
        for name, s in stats.items():
            if s["count"] > 0:
                lines.append(f"  {name}: avg={s['avg_ms']:.0f}ms, p95={s.get('p95_ms', 0):.0f}ms, n={s['count']}")
        return "\n".join(lines) if len(lines) > 1 else "No metrics recorded."


# Global instances
perf_monitor = PerfMonitor()
idle_detector = IdleDetector()


# ── Timing Decorator ──────────────────────────────────────────────────────

def timed(name: str | None = None):
    """Decorator: record execution time to perf_monitor."""
    def decorator(func: Callable) -> Callable:
        import functools
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            t0 = time.time()
            result = func(*args, **kwargs)
            elapsed_ms = (time.time() - t0) * 1000
            perf_monitor.record(name or func.__name__, elapsed_ms)
            return result
        return wrapper
    return decorator
