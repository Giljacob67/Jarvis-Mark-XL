"""
MARK XL — Location Awareness.

Provides geographic context for smarter responses:
  - IP-based location detection
  - Manual location setting
  - Weather integration
  - Timezone awareness
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from core.logger import get_logger
from core.paths import BASE_DIR

log = get_logger("location")

LOCATION_PATH = BASE_DIR / "config" / "location.json"
_cache: dict = {}
_cache_ttl = 3600  # 1 hour


def _load_location() -> dict:
    try:
        return json.loads(LOCATION_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_location(loc: dict) -> None:
    LOCATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCATION_PATH.write_text(json.dumps(loc, indent=2), encoding="utf-8")


def get_location() -> dict:
    """Get current location (cached or from config/IP)."""
    global _cache
    now = time.time()

    # Return cached if fresh
    if _cache and now - _cache.get("_ts", 0) < _cache_ttl:
        return _cache

    # Try saved location first
    saved = _load_location()
    if saved.get("city"):
        saved["_ts"] = now
        _cache = saved
        return saved

    # IP-based geolocation (fallback)
    try:
        import requests
        resp = requests.get("http://ip-api.com/json/", timeout=5)
        data = resp.json()
        if data.get("status") == "success":
            loc = {
                "city": data.get("city", ""),
                "region": data.get("regionName", ""),
                "country": data.get("country", ""),
                "lat": data.get("lat", 0),
                "lon": data.get("lon", 0),
                "timezone": data.get("timezone", ""),
                "ip": data.get("query", ""),
                "_ts": now,
            }
            _cache = loc
            return loc
    except Exception as e:
        log.warning("IP geolocation failed: %s", e)

    return {"city": "Unknown", "country": "Unknown", "_ts": now}


def set_location(city: str, region: str = "", country: str = "") -> str:
    """Manually set location."""
    loc = {
        "city": city,
        "region": region,
        "country": country,
        "lat": 0,
        "lon": 0,
        "timezone": "",
        "_ts": time.time(),
    }
    _save_location(loc)
    global _cache
    _cache = loc
    return f"Location set to {city}" + (f", {region}" if region else "") + (f", {country}" if country else "") + "."


def get_timezone() -> str:
    """Get the local timezone string."""
    loc = get_location()
    tz = loc.get("timezone", "")
    if tz:
        return tz
    return str(datetime.now().astimezone().tzinfo)


def get_context_string() -> str:
    """Get a location context string for the system prompt."""
    loc = get_location()
    parts = []
    if loc.get("city"):
        parts.append(loc["city"])
    if loc.get("region") and loc["region"] != loc.get("city"):
        parts.append(loc["region"])
    if loc.get("country"):
        parts.append(loc["country"])
    tz = get_timezone()
    now = datetime.now()
    return f"Location: {', '.join(parts) if parts else 'Unknown'}. Timezone: {tz}. Local time: {now.strftime('%H:%M')}."
