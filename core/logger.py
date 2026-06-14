"""
MARK XL — Structured logging.

Usage:
    from core.logger import get_logger
    log = get_logger(__name__)
    log.info("Ollama started")
    log.debug("Payload: %s", payload)
    log.warning("Model not found, using fallback")
    log.error("Connection failed: %s", e)
"""
from __future__ import annotations

import logging
import sys

_FORMAT = "[%(levelname)s] %(name)s: %(message)s"
_DATE_FMT = "%H:%M:%S"

_configured = False


def _configure() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Console handler — INFO and above for clean output
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE_FMT))
    root.addHandler(console)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger, configuring the root on first call."""
    _configure()
    return logging.getLogger(name)
