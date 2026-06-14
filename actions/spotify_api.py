"""
MARK XL — Spotify Web API integration.

Requires Spotify OAuth token in config/api_keys.json:
    "spotify_client_id": "...",
    "spotify_client_secret": "...",
    "spotify_access_token": "...",

Get token: https://developer.spotify.com/console/get-playlist/
"""
from __future__ import annotations

import json

import requests

from core.paths import API_CONFIG_PATH
from core.logger import get_logger

log = get_logger("spotify_api")


def _load_config() -> dict:
    try:
        return json.loads(API_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _get_headers() -> dict:
    cfg = _load_config()
    token = cfg.get("spotify_access_token", "")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _api_get(endpoint: str, params: dict | None = None) -> dict | None:
    """Make a GET request to Spotify API."""
    headers = _get_headers()
    if not headers:
        return None
    try:
        resp = requests.get(
            f"https://api.spotify.com/v1{endpoint}",
            headers=headers,
            params=params or {},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error("Spotify API error: %s", e)
        return None


def _api_post(endpoint: str, data: dict | None = None) -> bool:
    """Make a POST request to Spotify API."""
    headers = _get_headers()
    if not headers:
        return False
    try:
        resp = requests.post(
            f"https://api.spotify.com/v1{endpoint}",
            headers=headers,
            json=data or {},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("Spotify API error: %s", e)
        return False


# ── Public API ────────────────────────────────────────────────────────────

def search(query: str, search_type: str = "track", limit: int = 5) -> list[dict]:
    """Search Spotify for tracks, artists, albums, or playlists."""
    data = _api_get("/search", {"q": query, "type": search_type, "limit": limit})
    if not data:
        return []
    items_key = f"{search_type}s"
    return data.get(items_key, {}).get("items", [])


def get_current_playback() -> dict | None:
    """Get current playback state."""
    return _api_get("/me/player")


def get_playing_track() -> str | None:
    """Get currently playing track name."""
    data = get_current_playback()
    if not data or not data.get("item"):
        return None
    item = data["item"]
    name = item.get("name", "")
    artist = item.get("artists", [{}])[0].get("name", "")
    return f"{name} by {artist}"


def play(device_id: str | None = None, context_uri: str | None = None,
         uris: list[str] | None = None) -> bool:
    """Start/resume playback."""
    data = {}
    if device_id:
        data["device_ids"] = [device_id]
    if context_uri:
        data["context_uri"] = context_uri
    if uris:
        data["uris"] = uris
    return _api_put("/me/player/play", data)


def pause() -> bool:
    """Pause playback."""
    return _api_put("/me/player/pause")


def next_track() -> bool:
    """Skip to next track."""
    return _api_post("/me/player/next")


def previous_track() -> bool:
    """Skip to previous track."""
    return _api_post("/me/player/previous")


def get_top_tracks(limit: int = 10) -> list[dict]:
    """Get user's top tracks."""
    data = _api_get("/me/top/tracks", {"limit": limit, "time_range": "short_term"})
    if not data:
        return []
    return data.get("items", [])


def get_recently_played(limit: int = 10) -> list[dict]:
    """Get recently played tracks."""
    data = _api_get("/me/player/recently-played", {"limit": limit})
    if not data:
        return []
    return data.get("items", [])


def get_saved_playlists() -> list[dict]:
    """Get user's saved playlists."""
    data = _api_get("/me/playlists", {"limit": 20})
    if not data:
        return []
    return data.get("items", [])


def _api_put(endpoint: str, data: dict | None = None) -> bool:
    """Make a PUT request to Spotify API."""
    headers = _get_headers()
    if not headers:
        return False
    try:
        resp = requests.put(
            f"https://api.spotify.com/v1{endpoint}",
            headers=headers,
            json=data or {},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("Spotify API error: %s", e)
        return False
