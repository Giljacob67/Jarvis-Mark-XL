"""
core/tool_runtime.py — single source of truth for tool execution.

Replaces the duplicated giant ``if/elif`` dispatch blocks that used to live in:
  * ``main.JarvisLocal._execute_tool`` (the old ``_dispatch``)
  * ``agent.executor.AgentExecutor._call_tool``

Every action tool is routed through :func:`dispatch`, so adding, renaming or
removing a tool happens in exactly one place. The old ``executor`` behaviour of
silently falling back to *LLM-generated code* for any unknown tool is gone: an
unregistered tool now returns a clean error instead of executing arbitrary code.

Execution with a timeout (:func:`run_with_timeout`) genuinely interrupts hung
tools: it sets a cancel event the tool can cooperatively check, and — when
``psutil`` is available — kills only the child processes spawned during that
specific call (a plain ``Future.cancel()`` cannot stop an already-running
thread, so the process-group kill is the real safeguard).
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, TimeoutError as FuturesTimeout

from core.logger import get_logger

log = get_logger("tool_runtime")

DEFAULT_TIMEOUT = 45
DEFAULT_CONFIRM_TOOLS = frozenset({"send_message", "app_installer", "computer_control"})


class ToolContext:
    """Everything an action tool may need from the running assistant.

    A fresh context is created per call; it is never mutated by :func:`dispatch`.
    """

    __slots__ = ("player", "speak", "response", "session_memory", "ui",
                 "current_file", "cancel_event")

    def __init__(self, *, player=None, speak=None, response=None,
                 session_memory=None, ui=None, current_file=None):
        self.player = player
        self.speak = speak
        self.response = response
        self.session_memory = session_memory
        self.ui = ui
        self.current_file = current_file
        self.cancel_event = threading.Event()


def dispatch(name: str, args: dict, ctx: ToolContext) -> str:
    """Execute one registered action tool. Returns a string result.

    Raises :class:`KeyError` for unregistered tools so the caller decides how
    to report it. Mirrors the exact call signatures previously hard-coded in
    ``main._execute_tool`` and ``executor._call_tool``.
    """
    if name == "open_app":
        from actions.open_app import open_app
        return open_app(parameters=args, response=ctx.response, player=ctx.player) or "Done."

    if name == "web_search":
        from actions.web_search import web_search
        return web_search(parameters=args, player=ctx.player) or "Done."

    if name == "game_updater":
        from actions.game_updater import game_updater
        return game_updater(parameters=args, player=ctx.player, speak=ctx.speak) or "Done."

    if name == "browser_control":
        from actions.browser_control import browser_control
        return browser_control(parameters=args, player=ctx.player) or "Done."

    if name == "file_controller":
        from actions.file_controller import file_controller
        return file_controller(parameters=args, player=ctx.player) or "Done."

    if name == "code_helper":
        from actions.code_helper import code_helper
        return code_helper(parameters=args, player=ctx.player, speak=ctx.speak) or "Done."

    if name == "dev_agent":
        from actions.dev_agent import dev_agent
        return dev_agent(parameters=args, player=ctx.player, speak=ctx.speak) or "Done."

    if name == "screen_process":
        from actions.screen_processor import screen_process
        r = screen_process(parameters=args, response=ctx.response,
                           player=ctx.player, session_memory=ctx.session_memory)
        return r if isinstance(r, str) and r else "Screen captured and analyzed."

    if name == "send_message":
        from actions.send_message import send_message
        return send_message(parameters=args, response=ctx.response, player=ctx.player,
                           session_memory=ctx.session_memory) or "Done."

    if name == "reminder":
        from actions.reminder import reminder
        return reminder(parameters=args, response=ctx.response, player=ctx.player) or "Done."

    if name == "youtube_video":
        from actions.youtube_video import youtube_video
        return youtube_video(parameters=args, response=ctx.response, player=ctx.player) or "Done."

    if name == "weather_report":
        from actions.weather_report import weather_action
        return weather_action(parameters=args, player=ctx.player) or "Done."

    if name == "computer_settings":
        from actions.computer_settings import computer_settings
        return computer_settings(parameters=args, response=ctx.response, player=ctx.player) or "Done."

    if name == "desktop_control":
        from actions.desktop import desktop_control
        return desktop_control(parameters=args, player=ctx.player) or "Done."

    if name == "computer_control":
        from actions.computer_control import computer_control
        return computer_control(parameters=args, player=ctx.player) or "Done."

    if name == "flight_finder":
        from actions.flight_finder import flight_finder
        return flight_finder(parameters=args, player=ctx.player, speak=ctx.speak) or "Done."

    if name == "clipboard":
        from actions.clipboard_tool import clipboard_tool
        return clipboard_tool(parameters=args, player=ctx.player) or "Done."

    if name == "email_tool":
        from actions.email_tool import email_tool
        return email_tool(parameters=args, player=ctx.player, speak=ctx.speak) or "Done."

    if name == "spotify":
        from actions.spotify_tool import spotify_tool
        return spotify_tool(parameters=args, player=ctx.player, speak=ctx.speak) or "Done."

    if name == "notes":
        from actions.notes_tool import notes_tool
        return notes_tool(parameters=args, player=ctx.player, speak=ctx.speak) or "Done."

    if name == "translator":
        from actions.translator_tool import translator_tool
        return translator_tool(parameters=args, player=ctx.player, speak=ctx.speak) or "Done."

    if name == "timer":
        from actions.timer_tool import timer_tool
        return timer_tool(parameters=args, player=ctx.player, speak=ctx.speak) or "Done."

    if name == "calculator":
        from actions.calculator_tool import calculator_tool
        return calculator_tool(parameters=args, player=ctx.player, speak=ctx.speak) or "Done."

    if name == "notify":
        from actions.notify_tool import notify_tool
        return notify_tool(parameters=args, player=ctx.player) or "Done."

    if name == "app_installer":
        from actions.app_installer import app_installer
        return app_installer(parameters=args, player=ctx.player, speak=ctx.speak) or "Done."

    if name == "calendar":
        from actions.calendar_tool import calendar_tool
        return calendar_tool(parameters=args, player=ctx.player) or "Done."

    if name == "visual_web":
        from actions.visual_web import visual_web
        return visual_web(parameters=args, player=ctx.player, speak=ctx.speak) or "Done."

    if name == "smart_home":
        from actions.kasa_tool import kasa_tool
        return kasa_tool(parameters=args, player=ctx.player) or "Done."

    if name == "file_processor":
        params = dict(args)
        if not params.get("file_path") and ctx.current_file:
            params["file_path"] = ctx.current_file
        from actions.file_processor import file_processor
        return file_processor(parameters=params, player=ctx.player, speak=ctx.speak) or "Done."

    raise KeyError(name)


def run_with_timeout(name: str, args: dict, ctx: ToolContext, *,
                     timeout: int = DEFAULT_TIMEOUT,
                     executor) -> tuple[str | None, bool]:
    """Run ``name`` on ``executor``; kill child processes if it overruns.

    Returns ``(result, timed_out)``. On timeout the context's ``cancel_event``
    is set (tools may check it to abort cooperatively) and any child processes
    spawned *during this call* are terminated via psutil.
    """
    before = _child_pids()
    fut: Future = executor.submit(dispatch, name, args, ctx)
    try:
        return fut.result(timeout=timeout), False
    except FuturesTimeout:
        ctx.cancel_event.set()
        _kill_new_children(before)
        fut.cancel()  # best-effort; no-op for already-running threads
        return None, True
    except KeyError:
        return f"Unknown tool: {name}", False


# ── child-process tracking (best-effort, psutil optional) ─────────────────

def _child_pids() -> set[int]:
    try:
        import psutil
        return {p.pid for p in psutil.Process().children(recursive=True)}
    except Exception:
        return set()


def _kill_new_children(before: set[int]) -> None:
    """Kill only the processes that appeared after ``before`` was snapshotted."""
    try:
        import psutil
    except Exception:
        return
    try:
        now = {p.pid for p in psutil.Process().children(recursive=True)}
    except Exception:
        return
    for pid in now - before:
        try:
            psutil.Process(pid).kill()
        except Exception:
            pass
