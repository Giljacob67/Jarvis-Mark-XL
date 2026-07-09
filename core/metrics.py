"""
MARK XL — Lightweight observability.

A thread-safe, dependency-free collector for pipeline latencies (STT, LLM
time-to-first-token, tool execution). Numbers are kept in bounded rolling
windows so memory stays flat; :func:`snapshot` returns per-stage stats that
can be logged or surfaced in the dashboard.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from core.logger import get_logger

log = get_logger("metrics")

_WINDOW = 200


class _Metrics:
    def __init__(self, window: int = _WINDOW):
        self._lock = threading.Lock()
        self._data: dict[str, deque] = defaultdict(lambda: deque(maxlen=window))

    def record(self, name: str, ms: float) -> None:
        if ms is None or ms < 0:
            return
        with self._lock:
            self._data[name].append((time.time(), float(ms)))

    def snapshot(self) -> dict:
        with self._lock:
            out: dict = {}
            for k, q in self._data.items():
                if not q:
                    continue
                vals = [v for _, v in q]
                out[k] = {
                    "n": len(vals),
                    "last": round(vals[-1], 1),
                    "avg": round(sum(vals) / len(vals), 1),
                    "min": round(min(vals), 1),
                    "max": round(max(vals), 1),
                }
            return out

    def summary_line(self) -> str:
        snap = self.snapshot()
        if not snap:
            return "no metrics yet"
        return " | ".join(
            f"{k}: {s['avg']}ms (last {s['last']}ms, n={s['n']})"
            for k, s in snap.items()
        )


_metrics = _Metrics()


def record(name: str, ms: float) -> None:
    _metrics.record(name, ms)


def snapshot() -> dict:
    return _metrics.snapshot()


def summary_line() -> str:
    return _metrics.summary_line()
