"""App installer tool — brew / winget / apt cross-platform."""
import platform
import shutil
import subprocess
import sys

_OS = platform.system()


def _run(cmd: list[str], timeout: int = 120) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out."
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"


def _pkg_manager() -> str | None:
    if _OS == "Darwin":
        return "brew" if shutil.which("brew") else None
    if _OS == "Windows":
        return "winget" if shutil.which("winget") else None
    # Linux — prefer apt, fallback to dnf/pacman
    for pm in ("apt", "apt-get", "dnf", "pacman", "snap"):
        if shutil.which(pm):
            return pm
    return None


def _install(pkg: str, pm: str) -> str:
    if pm == "brew":
        code, out, err = _run(["brew", "install", pkg])
    elif pm == "winget":
        code, out, err = _run(["winget", "install", "--id", pkg, "-e", "--silent"])
    elif pm in ("apt", "apt-get"):
        code, out, err = _run(["sudo", pm, "install", "-y", pkg])
    elif pm == "dnf":
        code, out, err = _run(["sudo", "dnf", "install", "-y", pkg])
    elif pm == "pacman":
        code, out, err = _run(["sudo", "pacman", "-S", "--noconfirm", pkg])
    elif pm == "snap":
        code, out, err = _run(["sudo", "snap", "install", pkg])
    else:
        return f"Unsupported package manager: {pm}"
    if code == 0:
        return f"Installed '{pkg}' successfully, sir."
    return f"Install failed (code {code}): {err or out}"


def _uninstall(pkg: str, pm: str) -> str:
    if pm == "brew":
        code, out, err = _run(["brew", "uninstall", pkg])
    elif pm == "winget":
        code, out, err = _run(["winget", "uninstall", "--id", pkg, "-e", "--silent"])
    elif pm in ("apt", "apt-get"):
        code, out, err = _run(["sudo", pm, "remove", "-y", pkg])
    elif pm == "dnf":
        code, out, err = _run(["sudo", "dnf", "remove", "-y", pkg])
    elif pm == "pacman":
        code, out, err = _run(["sudo", "pacman", "-R", "--noconfirm", pkg])
    elif pm == "snap":
        code, out, err = _run(["sudo", "snap", "remove", pkg])
    else:
        return f"Unsupported package manager: {pm}"
    if code == 0:
        return f"Uninstalled '{pkg}', sir."
    return f"Uninstall failed: {err or out}"


def _search(pkg: str, pm: str) -> str:
    if pm == "brew":
        code, out, _ = _run(["brew", "search", pkg])
    elif pm == "winget":
        code, out, _ = _run(["winget", "search", pkg])
    elif pm in ("apt", "apt-get"):
        code, out, _ = _run(["apt-cache", "search", pkg])
    elif pm == "dnf":
        code, out, _ = _run(["dnf", "search", pkg])
    elif pm == "pacman":
        code, out, _ = _run(["pacman", "-Ss", pkg])
    else:
        return f"Search not supported for: {pm}"
    if not out:
        return f"No results for '{pkg}', sir."
    lines = out.splitlines()[:10]
    return "\n".join(lines)


def app_installer(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    params  = parameters or {}
    action  = params.get("action", "install").lower()
    package = params.get("package", "").strip()

    if not package:
        return "No package name provided, sir."

    pm = _pkg_manager()
    if not pm:
        return "No supported package manager found (brew/winget/apt/dnf/pacman), sir."

    if player:
        player.write_log(f"[AppInstaller] {action} {package} via {pm}")
    if speak:
        speak(f"{action.title()}ing {package}, sir. One moment.")

    if action == "install":
        return _install(package, pm)
    elif action == "uninstall":
        return _uninstall(package, pm)
    elif action == "search":
        return _search(package, pm)

    return f"Unknown action: {action}. Use install, uninstall, or search."
