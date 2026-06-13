"""Clipboard tool — read, write, clear."""
import platform


def clipboard_tool(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    try:
        import pyperclip
    except ImportError:
        return "pyperclip not installed. Run: pip install pyperclip"

    params = parameters or {}
    action = params.get("action", "read").lower()
    text   = params.get("text", "")

    if player:
        player.write_log(f"[Clipboard] {action}")

    if action == "read":
        content = pyperclip.paste()
        if not content:
            return "Clipboard is empty, sir."
        return f"Clipboard: {content}"

    elif action == "write":
        if not text:
            return "No text provided to write to clipboard, sir."
        pyperclip.copy(text)
        return "Copied to clipboard, sir."

    elif action == "clear":
        pyperclip.copy("")
        return "Clipboard cleared, sir."

    return f"Unknown clipboard action: {action}"
