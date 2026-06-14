"""
MARK XL — Calculator tool.

Evaluates mathematical expressions safely.

Actions:
    calculate <expression> — evaluate a math expression
"""
from __future__ import annotations

import math
import re

from core.logger import get_logger

log = get_logger("calculator")

# Safe math functions allowed in expressions
_SAFE_MATH = {
    "abs": abs, "round": round, "min": min, "max": max,
    "sqrt": math.sqrt, "pow": pow, "log": math.log, "log10": math.log10,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "pi": math.pi, "e": math.e, "inf": math.inf,
}


def calculator_tool(
    parameters: dict,
    player=None,
    speak=None,
) -> str:
    params = parameters or {}
    expression = params.get("expression", params.get("text", ""))

    if player:
        player.write_log("SYS: Calculator")

    if not expression:
        return "No expression provided."

    # Clean the expression
    expr = expression.strip()
    expr = expr.replace("x", "*").replace("X", "*")
    expr = expr.replace("÷", "/").replace("×", "*")
    expr = expr.replace("^", "**")  # math notation → Python
    # Allow digits, operators, parens, dots, commas, and letters (for function names)
    expr = re.sub(r'[^0-9+\-*/().,%a-zA-Z]', '', expr)

    try:
        # Evaluate safely with restricted globals
        result = eval(expr, {"__builtins__": {}}, _SAFE_MATH)
        return f"{expression} = {result}"
    except Exception as e:
        log.warning("Calculator error: %s", e)
        return f"Could not calculate '{expression}': {e}"
