"""Structured error types for Jarvis-Mark-XL."""


class JarvisError(Exception):
    """Base for all Jarvis errors."""


class JarvisNetworkError(JarvisError):
    """Network connectivity failure — safe to retry with backoff."""


class JarvisTimeoutError(JarvisError):
    """Operation exceeded its time limit."""


class JarvisPermissionError(JarvisError):
    """Insufficient OS permissions — retry will not help."""


class JarvisToolError(JarvisError):
    """A tool raised an unexpected error."""


class JarvisLLMError(JarvisError):
    """LLM unavailable or returned an unusable response."""


def classify_exception(exc: Exception) -> type:
    """Map a raw exception to a Jarvis error class."""
    msg = str(exc).lower()
    if any(k in msg for k in ("connection", "network", "dns", "refused", "unreachable")):
        return JarvisNetworkError
    if any(k in msg for k in ("timeout", "timed out")):
        return JarvisTimeoutError
    if any(k in msg for k in ("permission", "access denied", "eperm", "eacces")):
        return JarvisPermissionError
    if any(k in msg for k in ("ollama", "llm", "model", "chat")):
        return JarvisLLMError
    return JarvisToolError
