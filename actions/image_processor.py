"""
MARK XL — Image Processor for multi-modal input.

Processes images uploaded from the phone or desktop:
  - Describes images via vision-capable LLM (Ollama llava, etc.)
  - Extracts text via OCR (if available)
  - Feeds image context into the conversation
"""
from __future__ import annotations

import base64
from pathlib import Path

from core.logger import get_logger

log = get_logger("image_processor")


def describe_image(
    image_path: str | Path,
    prompt: str = "Describe this image in detail.",
    model: str | None = None,
) -> str:
    """
    Describe an image using a vision-capable LLM via Ollama.

    Args:
        image_path: Path to the image file
        prompt: Question/prompt about the image
        model: Ollama model name (defaults to llava or config)

    Returns:
        Text description of the image
    """
    image_path = Path(image_path)
    if not image_path.exists():
        return f"Image not found: {image_path}"

    # Read and encode image
    try:
        with open(image_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        return f"Failed to read image: {e}"

    # Determine model
    if model is None:
        try:
            import json
            config_path = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            model = config.get("vision_model", "llava")
        except Exception:
            model = "llava"

    # Call Ollama vision API
    try:
        import requests
        url = "http://localhost:11434/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "images": [image_b64],
            "stream": False,
        }
        resp = requests.post(url, json=payload, timeout=120)
        resp.raise_for_status()
        result = resp.json()
        return result.get("response", "No description generated.")
    except Exception as e:
        log.error("Vision LLM call failed: %s", e)
        return f"Vision analysis failed: {e}"


def get_image_info(image_path: str | Path) -> dict:
    """Get basic metadata about an image."""
    image_path = Path(image_path)
    info = {
        "path": str(image_path),
        "name": image_path.name,
        "size": image_path.stat().st_size if image_path.exists() else 0,
        "exists": image_path.exists(),
    }

    if image_path.exists():
        try:
            from PIL import Image
            img = Image.open(image_path)
            info["format"] = img.format
            info["mode"] = img.mode
            info["width"] = img.width
            info["height"] = img.height
        except Exception:
            pass

    return info


def process_image_upload(
    image_path: str | Path,
    user_prompt: str | None = None,
    model: str | None = None,
) -> str:
    """
    Process an uploaded image: get info + describe via vision LLM.

    Returns a formatted string with image analysis results.
    """
    info = get_image_info(image_path)

    if not info["exists"]:
        return f"Image not found: {image_path}"

    parts = [f"Image: {info['name']}"]
    if "width" in info:
        parts.append(f"Size: {info['width']}x{info['height']}")
    if "format" in info:
        parts.append(f"Format: {info['format']}")
    parts.append(f"File size: {info['size'] / 1024:.1f} KB")

    prompt = user_prompt or "Describe this image in detail. What do you see?"
    description = describe_image(image_path, prompt=prompt, model=model)
    parts.append(f"\nDescription: {description}")

    return "\n".join(parts)
