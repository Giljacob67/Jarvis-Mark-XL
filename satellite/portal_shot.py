"""
Captura de tela silenciosa via xdg-desktop-portal (GNOME/Wayland).

Uso: /usr/bin/python3 portal_shot.py <destino.png>

Roda com o Python do SISTEMA (precisa de python3-gi, que não existe na
venv do satélite). Exige permissão pré-concedida na permission store —
uma vez, no desktop:  flatpak permission-set screenshot screenshot '' yes
Sem ela o GNOME abre diálogo interativo e a captura autônoma falha.

Ressalva de privacidade: o portal grava primeiro em XDG_PICTURES_DIR
(~/Imagens); movemos imediatamente para o destino (em /dev/shm o satélite
apaga no finally), então nenhuma cópia sobra — mas o PNG toca o disco por
um instante, é limitação do portal.
"""
import random
import shutil
import string
import sys
from urllib.parse import unquote, urlparse

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

TIMEOUT_S = 12


def portal_screenshot() -> str:
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    sender = bus.get_unique_name()[1:].replace(".", "_")
    token = "jarvis" + "".join(random.choices(string.ascii_lowercase, k=8))
    request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

    loop = GLib.MainLoop()
    result: dict = {}

    def on_response(conn, sender_name, path, iface, signal, params):
        code, results = params.unpack()
        result["code"] = code
        result["uri"] = results.get("uri", "")
        loop.quit()

    bus.signal_subscribe(
        "org.freedesktop.portal.Desktop",
        "org.freedesktop.portal.Request",
        "Response",
        request_path,
        None,
        Gio.DBusSignalFlags.NO_MATCH_RULE,
        on_response,
    )

    bus.call_sync(
        "org.freedesktop.portal.Desktop",
        "/org/freedesktop/portal/desktop",
        "org.freedesktop.portal.Screenshot",
        "Screenshot",
        GLib.Variant("(sa{sv})", ("", {
            "handle_token": GLib.Variant("s", token),
            "interactive": GLib.Variant("b", False),
        })),
        GLib.VariantType("(o)"),
        Gio.DBusCallFlags.NONE,
        TIMEOUT_S * 1000,
        None,
    )

    GLib.timeout_add_seconds(TIMEOUT_S, loop.quit)
    loop.run()

    if result.get("code") != 0 or not result.get("uri"):
        raise RuntimeError(f"portal respondeu {result or 'timeout'}")
    return result["uri"]


def main() -> int:
    dest = sys.argv[1]
    uri = portal_screenshot()
    src = unquote(urlparse(uri).path)
    shutil.move(src, dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
