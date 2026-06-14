"""
MARK XL — Central security gate for code-execution capabilities.

Several tools can run arbitrary or LLM-generated code (desktop generated-code,
the agent executor's generated_code path, code_helper run/build, dev_agent).
On a machine that touches confidential data these are a liability, so they are
DISABLED BY DEFAULT and only enabled when the user explicitly opts in via:

    config/api_keys.json  ->  "allow_code_execution": true

Every entry point that executes code MUST check `code_execution_allowed()`
before running, and return `CODE_EXEC_DISABLED_MSG` (or raise) when it is off.
"""
from __future__ import annotations

import json

from core.paths import API_CONFIG_PATH as _CONFIG_PATH

# User-facing message returned whenever a gated capability is invoked while off.
CODE_EXEC_DISABLED_MSG = (
    "Code execution is disabled for safety. "
    'Enable it by setting "allow_code_execution": true in config/api_keys.json.'
)


def code_execution_allowed() -> bool:
    """
    True only when the user has explicitly opted in. Any error reading the
    config (missing file, malformed JSON, missing key) fails CLOSED -> False.
    """
    try:
        cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(cfg.get("allow_code_execution", False))
