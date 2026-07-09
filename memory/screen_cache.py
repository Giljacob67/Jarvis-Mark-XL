"""
MARK XL — Screen cache for continuous vision.

Stores the most recent screen/camera description so the assistant can answer
"what changed on screen?" by diffing the current capture against the previous
one. Fail-closed: any error returns empty values, never raises.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from core.paths import BASE_DIR

_PATH = BASE_DIR / "memory" / "screen_cache.json"


def save(description: str) -> None:
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(
            json.dumps(
                {"description": description, "ts": time.time()},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def get_last() -> tuple[str, float]:
    """Return (last_description, timestamp). ('', 0.0) when none / on error."""
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        return data.get("description", ""), float(data.get("ts", 0.0))
    except Exception:
        return "", 0.0
