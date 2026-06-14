"""
MARK XL — Spotify tool.

Controls Spotify via the system's default browser or Spotify desktop app.
Requires Spotify running (desktop or web).

Actions:
    play [query]   — play a song/artist/album (opens Spotify search)
    pause          — pause playback
    resume         — resume playback
    next           — next track
    previous       — previous track
    status         — show what's playing (via osascript on macOS)
"""
from __future__ import annotations

import platform
import subprocess

from core.logger import get_logger

log = get_logger("spotify")

_OS = platform.system()


def _osascript(script: str) -> str:
    """Run an AppleScript on macOS."""
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip()
    except Exception as e:
        return f"Error: {e}"


def _open_spotify_url(query: str) -> str:
    """Open Spotify search in browser."""
    import webbrowser
    from urllib.parse import quote_plus
    url = f"https://open.spotify.com/search/{quote_plus(query)}"
    webbrowser.open(url)
    return f"Opened Spotify search: {query}"


def spotify_tool(
    parameters: dict,
    player=None,
    speak=None,
) -> str:
    params = parameters or {}
    action = params.get("action", "play").lower()
    query = params.get("query", "")

    if player:
        player.write_log(f"SYS: Spotify — {action}")

    if _OS == "Darwin":
        if action == "play":
            if query:
                _osascript(f'tell application "Spotify" to search "{query}"')
                return f"Playing: {query}"
            else:
                _osascript('tell application "Spotify" to play')
                return "Resumed playback."

        elif action == "pause":
            _osascript('tell application "Spotify" to pause')
            return "Paused."

        elif action == "resume":
            _osascript('tell application "Spotify" to play')
            return "Resumed."

        elif action == "next":
            _osascript('tell application "Spotify" to next track')
            return "Next track."

        elif action == "previous":
            _osascript('tell application "Spotify" to previous track')
            return "Previous track."

        elif action == "status":
            name = _osascript('tell application "Spotify" to name of current track')
            artist = _osascript('tell application "Spotify" to artist of current track')
            playing = _osascript('tell application "Spotify" to player state')
            if "stopped" in playing.lower():
                return "Spotify is not playing."
            return f"Now playing: {name} by {artist}"

    # Fallback: open in browser
    if action == "play" and query:
        return _open_spotify_url(query)

    return f"Spotify {action} not supported on {_OS}. Try opening via browser."
