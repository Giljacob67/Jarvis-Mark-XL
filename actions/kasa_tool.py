"""
Smart Home — TP-Link Kasa enhanced tool.
Requires: pip install python-kasa
Config: "kasa_devices": [{"alias": "Luz sala", "ip": "192.168.1.x"}]

Features:
  - Auto-discover devices on LAN
  - Power on/off/toggle
  - Brightness/color control
  - Energy monitoring
  - Device status with real-time info
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


async def _discover_devices() -> list[dict]:
    """Discover all TP-Link Kasa devices on the local network."""
    try:
        from kasa import Discover
    except ImportError:
        raise RuntimeError("python-kasa not installed. Run: pip install python-kasa")

    devices = await Discover.discover()
    result = []
    for addr, dev in devices.items():
        await dev.update()
        info = {
            "ip": addr,
            "alias": dev.alias,
            "model": dev.model,
            "is_on": dev.is_on,
            "type": type(dev).__name__,
        }
        if hasattr(dev, "brightness"):
            info["brightness"] = dev.brightness
        if hasattr(dev, "color_temp"):
            info["color_temp"] = dev.color_temp
        result.append(info)
    return result


async def _run_action(ip: str, action: str, value=None) -> str:
    try:
        from kasa import SmartDevice
    except ImportError:
        raise RuntimeError("python-kasa not installed. Run: pip install python-kasa")

    dev = await SmartDevice.connect(host=ip)
    await dev.update()

    can_dim = hasattr(dev, "set_brightness")
    can_color = hasattr(dev, "set_color_temp") or hasattr(dev, "set_hsv")

    if action == "power_on":
        await dev.turn_on()
        return f"Turned on {dev.alias}."

    elif action == "power_off":
        await dev.turn_off()
        return f"Turned off {dev.alias}."

    elif action == "toggle":
        if dev.is_on:
            await dev.turn_off()
            return f"Turned off {dev.alias}."
        else:
            await dev.turn_on()
            return f"Turned on {dev.alias}."

    elif action == "set_brightness":
        if not can_dim:
            return f"{dev.alias} does not support brightness."
        level = max(1, min(100, int(value or 50)))
        await dev.set_brightness(level)
        return f"Set {dev.alias} brightness to {level}%."

    elif action == "set_color":
        if not can_color:
            return f"{dev.alias} does not support color."
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
            return f"Set {dev.alias} color to {value}."
        return f"Unknown color: {value}. Use a name or H,S,V format."

    elif action == "status":
        state = "on" if dev.is_on else "off"
        info = f"{dev.alias} is {state}"
        if can_dim and hasattr(dev, "brightness"):
            info += f", brightness {dev.brightness}%"
        return info + "."

    elif action == "energy":
        if hasattr(dev, "get_realtime"):
            rt = await dev.get_realtime()
            return (f"{dev.alias} energy: {rt.get('power_mw', 0)/1000:.1f}W, "
                    f"today: {rt.get('total_wh', 0)/1000:.2f}Wh")
        return f"{dev.alias} does not support energy monitoring."

    elif action == "info":
        info_parts = [
            f"Alias: {dev.alias}",
            f"Model: {dev.model}",
            f"IP: {dev.host}",
            f"State: {'on' if dev.is_on else 'off'}",
        ]
        if hasattr(dev, "brightness"):
            info_parts.append(f"Brightness: {dev.brightness}%")
        if hasattr(dev, "color_temp"):
            info_parts.append(f"Color temp: {dev.color_temp}")
        if hasattr(dev, "features"):
            info_parts.append(f"Features: {', '.join(str(f) for f in dev.features)}")
        return "\n".join(info_parts)

    return f"Unknown action: {action}"


async def _discover_and_cache() -> str:
    """Discover devices and save to config."""
    devices = await _discover_devices()
    if not devices:
        return "No Kasa devices found on the network."

    cfg = _load_config()
    existing_ips = {d.get("ip") for d in cfg.get("kasa_devices", [])}

    new_devices = []
    for d in devices:
        if d["ip"] not in existing_ips:
            new_devices.append({"alias": d["alias"], "ip": d["ip"]})

    # Merge into config
    all_devices = cfg.get("kasa_devices", []) + new_devices
    cfg["kasa_devices"] = all_devices

    # Save config
    config_path = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
    try:
        config_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        pass

    lines = [f"Found {len(devices)} device(s):"]
    for d in devices:
        state = "ON" if d["is_on"] else "OFF"
        saved = " (saved)" if d["ip"] not in existing_ips else " (known)"
        lines.append(f"  {d['alias']} ({d['model']}) — {state} @ {d['ip']}{saved}")
    return "\n".join(lines)


def kasa_tool(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    device = params.get("device", "").strip()
    action = params.get("action", "status").lower()
    value = params.get("value")

    # Discover action doesn't need a device
    if action == "discover":
        try:
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(_discover_and_cache())
            loop.close()
            return result
        except Exception as e:
            return f"Discovery failed: {e}"

    if not device:
        return "No device specified. Use action 'discover' to find devices."

    cfg = _load_config()
    kasa_devs = cfg.get("kasa_devices", [])

    resolved = _resolve_device(kasa_devs, device)
    if not resolved:
        return (
            f"Device '{device}' not found. Use action 'discover' to find devices, "
            "or add it under 'kasa_devices' in api_keys.json."
        )

    if player:
        player.write_log(f"[Kasa] {action} -> {device}")

    try:
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(_run_action(resolved["ip"], action, value))
        loop.close()
        return result
    except Exception as e:
        return f"Kasa command failed: {e}"
