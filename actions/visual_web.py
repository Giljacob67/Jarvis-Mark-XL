"""
Visual Web tool — screenshot → vision LLM → pyautogui action → loop.
Inspired by ada_v2 WebAgent. Uses local Ollama vision model (llava/moondream).

Config: "vision_model": "llava:7b" in api_keys.json
"""
import base64
import json
import re
import sys
import time
from pathlib import Path

import requests

_MAX_TURNS = 15


def _load_config() -> dict:
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    cfg_path = base / "config" / "api_keys.json"
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _screenshot_b64() -> str:
    """Capture screen and return base64-encoded PNG."""
    try:
        import mss, mss.tools
        with mss.mss() as sct:
            monitor = sct.monitors[0]
            img = sct.grab(monitor)
            png = mss.tools.to_png(img.rgb, img.size)
        return base64.b64encode(png).decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"Screenshot failed: {e}")


def _ask_vision(b64_img: str, goal: str, history: list, cfg: dict) -> dict:
    """Send screenshot to Ollama vision model. Returns action dict."""
    url    = cfg.get("llm_url", "http://localhost:11434").rstrip("/")
    model  = cfg.get("vision_model", "llava:7b")

    system = (
        "You are a web automation agent. Analyze the screenshot and decide the next action.\n"
        "Respond ONLY with a JSON object — no extra text:\n"
        '{"action": "click|type|scroll|key|done", '
        '"x": 0.5, "y": 0.5, "text": "", "key": "", "direction": "up|down", "result": ""}\n'
        "Coordinates are normalized 0.0–1.0 (x=left→right, y=top→bottom).\n"
        'Use "done" when the goal is achieved. Include "result" only with "done".'
    )
    messages = [{"role": "system", "content": system}]
    for h in history[-4:]:   # last 4 turns for context
        messages.append(h)
    messages.append({
        "role": "user",
        "content": [
            {"type": "text", "text": f"Goal: {goal}\nWhat is the next action?"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}},
        ],
    })

    payload = {
        "model":   model,
        "messages": messages,
        "stream":  False,
        "options": {"num_predict": 150},
    }
    resp = requests.post(f"{url}/api/chat", json=payload, timeout=60)
    resp.raise_for_status()
    raw = resp.json().get("message", {}).get("content", "{}").strip()
    # strip markdown fences if present
    raw = re.sub(r"```(?:json)?|```", "", raw).strip()
    return json.loads(raw)


def _denormalize(x: float, y: float) -> tuple[int, int]:
    """Convert 0–1 coordinates to screen pixels."""
    try:
        import pyautogui
        w, h = pyautogui.size()
        return int(x * w), int(y * h)
    except Exception:
        return int(x * 1920), int(y * 1080)


def _execute_action(action: dict) -> str:
    try:
        import pyautogui
    except ImportError:
        raise RuntimeError("pyautogui not installed.")

    atype = action.get("action", "done")
    x, y  = _denormalize(float(action.get("x", 0.5)), float(action.get("y", 0.5)))

    if atype == "click":
        pyautogui.moveTo(x, y, duration=0.3)
        pyautogui.click()
        return f"Clicked ({x},{y})"

    elif atype == "type":
        text = action.get("text", "")
        pyautogui.write(text, interval=0.05)
        return f"Typed: {text[:40]}"

    elif atype == "scroll":
        direction = action.get("direction", "down")
        amount    = 3 if direction == "down" else -3
        pyautogui.scroll(amount, x=x, y=y)
        return f"Scrolled {direction}"

    elif atype == "key":
        key = action.get("key", "")
        if key:
            pyautogui.press(key)
            return f"Pressed: {key}"

    elif atype == "done":
        return "__DONE__"

    return f"Unknown action: {atype}"


def visual_web(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    params = parameters or {}
    goal   = params.get("goal", "").strip()
    if not goal:
        return "No goal provided for visual web, sir."

    cfg = _load_config()
    if not cfg.get("vision_model"):
        return (
            "No vision model configured, sir. "
            "Add 'vision_model': 'llava:7b' to config and run: ollama pull llava:7b"
        )

    if player:
        player.write_log(f"[VisualWeb] Goal: {goal}")
    if speak:
        speak(f"Starting visual web task: {goal[:60]}, sir.")

    history: list = []
    for turn in range(1, _MAX_TURNS + 1):
        if player:
            player.write_log(f"[VisualWeb] Turn {turn}/{_MAX_TURNS}")

        try:
            b64 = _screenshot_b64()
            action = _ask_vision(b64, goal, history, cfg)
        except Exception as e:
            return f"Visual web failed at turn {turn}: {e}"

        history.append({"role": "assistant", "content": json.dumps(action)})
        print(f"[VisualWeb] Turn {turn}: {action}")

        try:
            result = _execute_action(action)
        except Exception as e:
            return f"Action execution failed: {e}"

        if result == "__DONE__":
            final = action.get("result", "Task completed.")
            if speak:
                speak(final + " Sir.")
            return final

        history.append({"role": "user", "content": f"Action result: {result}"})
        time.sleep(0.8)  # let UI settle

    return f"Visual web reached max turns ({_MAX_TURNS}) without completing the goal, sir."
