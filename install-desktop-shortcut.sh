#!/usr/bin/env bash
# Instala atalho do JARVIS na Área de trabalho e no menu de aplicativos.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
DESKTOP="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
APPS_DIR="$HOME/.local/share/applications"
SRC="$ROOT/Jarvis-Mark-XL.desktop"

mkdir -p "$APPS_DIR"
cp "$SRC" "$DESKTOP/JARVIS Mark XL.desktop"
cp "$SRC" "$APPS_DIR/jarvis-mark-xl.desktop"
chmod +x "$ROOT/run.sh"
chmod +x "$DESKTOP/JARVIS Mark XL.desktop"
chmod +x "$APPS_DIR/jarvis-mark-xl.desktop"

# GNOME: marcar como confiável para permitir duplo clique
if command -v gio >/dev/null 2>&1; then
  gio set "$DESKTOP/JARVIS Mark XL.desktop" metadata::trusted true 2>/dev/null || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APPS_DIR" 2>/dev/null || true
fi

echo "Atalho criado:"
echo "  • $DESKTOP/JARVIS Mark XL.desktop"
echo "  • $APPS_DIR/jarvis-mark-xl.desktop (menu do sistema)"