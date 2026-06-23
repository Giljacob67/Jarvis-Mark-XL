"""
MARK XL — Robust Error Handling.

Graceful degradation, retry logic, and comprehensive error recovery
for all tool executions and system components.
"""
from __future__ import annotations

import functools
import time
import traceback
from typing import Callable, TypeVar

from core.logger import get_logger

log = get_logger("error_handling")

T = TypeVar("T")


def retry_on_failure(
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
) -> Callable:
    """Decorator: retry a function on failure with exponential backoff."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            current_delay = delay
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        log.warning(
                            "Attempt %d/%d failed for %s: %s. Retrying in %.1fs...",
                            attempt + 1, max_retries, func.__name__, e, current_delay,
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        log.error(
                            "All %d attempts failed for %s: %s",
                            max_retries + 1, func.__name__, e,
                        )
            raise last_exception
        return wrapper
    return decorator


def safe_execute(func: Callable, *args, default: str = "", **kwargs) -> str:
    """
    Execute a function safely, returning a default value on failure.
    Never raises — always returns a result string.
    """
    try:
        result = func(*args, **kwargs)
        return result if isinstance(result, str) else str(result)
    except Exception as e:
        log.error("Safe execute failed for %s: %s", func.__name__, e)
        return default or f"Operation failed: {e}"


def format_error(error: Exception, context: str = "") -> str:
    """Format an error for user display (safe, no secrets)."""
    error_type = type(error).__name__
    error_msg = str(error).strip()

    # Truncate very long errors
    if len(error_msg) > 200:
        error_msg = error_msg[:200] + "..."

    parts = []
    if context:
        parts.append(f"{context}:")
    parts.append(f"{error_type}: {error_msg}" if error_msg else error_type)
    return " ".join(parts)


class ToolErrorHandler:
    """Context manager for tool execution with comprehensive error handling."""

    def __init__(self, tool_name: str, speak_fn: Callable | None = None):
        self.tool_name = tool_name
        self.speak_fn = speak_fn
        self._start_time = 0

    def __enter__(self):
        self._start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self._start_time
        if exc_type is not None:
            log.error("Tool %s failed after %.2fs: %s", self.tool_name, duration, exc_val)
            if self.speak_fn:
                try:
                    self.speak_fn(f"Tool error: {exc_val}")
                except Exception:
                    pass
        else:
            log.info("Tool %s completed in %.2fs", self.tool_name, duration)
        return False  # Don't suppress exceptions


def with_graceful_degradation(
    func: Callable | None = None,
    *,
    fallback_value: str = "",
    fallback_fn: Callable | None = None,
    log_error: bool = True,
):
    """
    Decorator: execute function with graceful degradation.
    On failure, returns fallback_value or calls fallback_fn.
    """
    def decorator(f: Callable) -> Callable:
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            try:
                return f(*args, **kwargs)
            except Exception as e:
                if log_error:
                    log.warning("Graceful degradation for %s: %s", f.__name__, e)
                if fallback_fn:
                    try:
                        return fallback_fn(*args, **kwargs)
                    except Exception:
                        pass
                return fallback_value
        return wrapper

    if func is not None:
        return decorator(func)
    return decorator
