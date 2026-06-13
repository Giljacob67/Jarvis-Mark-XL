"""
Smart Home — TP-Link Kasa tool.
Requires: pip install python-kasa
Config: "kasa_devices": [{"alias": "Luz sala", "ip": "192.168.1.x"}]
Inspired by ada_v2 KasaAgent.
"""
import asyncio
import json
import sys
from pathlib import Path


def _load_config() -> dict:
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    try:
        return json.loads((base / "config" / "api_keys.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_device(devices: list[dict], name: str) -> dict | None:
    name_low = name.lower()
    for d in devices:
        if d.get("alias", "").lower() == name_low:
            return d
        if d.get("ip", "") == name:
            return d
    return None


async def _run_action(ip: str, action: str, value=None) -> str:
    try:
        from kasa import SmartDevice, SmartBulb, SmartPlug
    except ImportError:
        raise RuntimeError("python-kasa not installed. Run: pip install python-kasa")

    dev = await SmartDevice.connect(host=ip)
    await dev.update()

    can_dim   = hasattr(dev, "set_brightness")
    can_color = hasattr(dev, "set_color_temp") or hasattr(dev, "set_hsv")

    if action == "power_on":
        await dev.turn_on()
        return f"Turned on {dev.alias}, sir."

    elif action == "power_off":
        await dev.turn_off()
        return f"Turned off {dev.alias}, sir."

    elif action == "toggle":
        if dev.is_on:
            await dev.turn_off()
            return f"Turned off {dev.alias}, sir."
        else:
            await dev.turn_on()
            return f"Turned on {dev.alias}, sir."

    elif action == "set_brightness":
        if not can_dim:
            return f"{dev.alias} does not support brightness, sir."
        level = max(1, min(100, int(value or 50)))
        await dev.set_brightness(level)
        return f"Set {dev.alias} brightness to {level}%, sir."

    elif action == "set_color":
        if not can_color:
            return f"{dev.alias} does not support color, sir."
        # value can be a CSS color name or "H,S,V"
        _COLOR_MAP = {
            "red": (0, 100, 100), "green": (120, 100, 100), "blue": (240, 100, 100),
            "white": (0, 0, 100), "warm": (30, 60, 100), "yellow": (60, 100, 100),
            "purple": (270, 100, 100), "cyan": (180, 100, 100), "orange": (30, 100, 100),
        }
        hsv = _COLOR_MAP.get(str(value).lower())
        if hsv is None:
            parts = str(value).split(",")
            if len(parts) == 3:
                hsv = (int(parts[0]), int(parts[1]), int(parts[2]))
        if hsv:
            await dev.set_hsv(*hsv)
            return f"Set {dev.alias} color to {value}, sir."
        return f"Unknown color: {value}. Use a name or H,S,V format."

    elif action == "status":
        state = "on" if dev.is_on else "off"
        info  = f"{dev.alias} is {state}"
        if can_dim and hasattr(dev, "brightness"):
            info += f", brightness {dev.brightness}%"
        return info + ", sir."

    return f"Unknown action: {action}"


def kasa_tool(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    device = params.get("device", "").strip()
    action = params.get("action", "status").lower()
    value  = params.get("value")

    if not device:
        return "No device specified, sir."

    cfg        = _load_config()
    kasa_devs  = cfg.get("kasa_devices", [])

    resolved = _resolve_device(kasa_devs, device)
    if not resolved:
        return (
            f"Device '{device}' not found in config, sir. "
            "Add it under 'kasa_devices' in api_keys.json."
        )

    if player:
        player.write_log(f"[Kasa] {action} → {device}")

    try:
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(_run_action(resolved["ip"], action, value))
        loop.close()
        return result
    except Exception as e:
        return f"Kasa command failed: {e}"
