"""Desktop notification tool — cross-platform."""
import platform
import subprocess
import sys

_OS = platform.system()


def notify_tool(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params  = parameters or {}
    title   = params.get("title", "Jarvis")
    message = params.get("message", "")
    sound   = params.get("sound", False)

    if not message:
        return "No message provided for notification, sir."

    if player:
        player.write_log(f"[Notify] {title}: {message}")

    try:
        if _OS == "Darwin":
            sound_part = "with sound" if sound else ""
            script = (
                f'display notification "{message}" '
                f'with title "{title}" {sound_part}'
            )
            subprocess.run(["osascript", "-e", script], capture_output=True)

        elif _OS == "Windows":
            try:
                from win10toast import ToastNotifier
                ToastNotifier().show_toast(title, message, duration=5, threaded=True)
            except ImportError:
                # Fallback: PowerShell notification
                ps = (
                    f"[Windows.UI.Notifications.ToastNotificationManager, "
                    f"Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null; "
                    f"$template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02; "
                    f"$xml = [Windows.UI.Notifications.ToastNotificationManager]"
                    f"::GetTemplateContent($template); "
                    f"$xml.GetElementsByTagName('text')[0].AppendChild($xml.CreateTextNode('{title}')) | Out-Null; "
                    f"$xml.GetElementsByTagName('text')[1].AppendChild($xml.CreateTextNode('{message}')) | Out-Null; "
                    f"$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); "
                    f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Jarvis').Show($toast)"
                )
                subprocess.run(
                    ["powershell", "-WindowStyle", "Hidden", "-Command", ps],
                    capture_output=True, timeout=5
                )

        else:  # Linux
            subprocess.run(
                ["notify-send", title, message],
                capture_output=True, timeout=5
            )

        return f"Notification sent: {title}"

    except Exception as e:
        return f"Notification failed: {e}"
