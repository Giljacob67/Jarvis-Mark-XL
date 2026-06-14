#!/bin/bash
# Creates "Jarvis Mark XL.app" in ~/Applications.
# Run once: bash scripts/create_app.sh
# Then drag the .app to your Dock.
set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="Jarvis Mark XL"
DEST="$HOME/Applications/$APP_NAME.app"

# Pick python3: venv > homebrew > system
if [ -f "$REPO_DIR/.venv/bin/python3" ]; then
    PYTHON="$REPO_DIR/.venv/bin/python3"
elif [ -f "/opt/homebrew/bin/python3" ]; then
    PYTHON="/opt/homebrew/bin/python3"
elif command -v python3 &>/dev/null; then
    PYTHON="$(command -v python3)"
else
    echo "ERROR: Python 3 not found."
    exit 1
fi

# Derive PyQt6 plugin path from the venv Python version.
# Use Python itself so the version string is always correct.
PY_VER="$("$PYTHON" -c "import sys; print(f'python{sys.version_info.major}.{sys.version_info.minor}')")"
PYQT6_DIR="$REPO_DIR/.venv/lib/$PY_VER/site-packages/PyQt6"
PYQT6_PLUGINS="$PYQT6_DIR/Qt6/plugins"

# The PyQt6-Qt6 wheel ships its plugin files with the macOS UF_HIDDEN flag set.
# QDir.entryList() (used by Qt's QFactoryLoader to scan for platform plugins)
# excludes UF_HIDDEN entries, so Qt finds zero plugins and aborts with
# "Could not find the Qt platform plugin cocoa". Clearing the flag fixes it.
# Re-run after any `pip install/reinstall` of PyQt6-Qt6.
if [ -d "$PYQT6_DIR" ]; then
    chflags -R nohidden "$PYQT6_DIR" 2>/dev/null || true
fi

echo "→ Repo:    $REPO_DIR"
echo "→ Python:  $PYTHON ($PY_VER)"
echo "→ Plugins: ${PYQT6_PLUGINS}"
echo "→ App:     $DEST"

mkdir -p "$HOME/Applications"
rm -rf "$DEST"
mkdir -p "$DEST/Contents/MacOS"
mkdir -p "$DEST/Contents/Resources"

# ── Launcher script ───────────────────────────────────────────────────────────
# Qt's Cocoa platform plugin (libqcocoa.dylib) crashes when launched from a
# launchd app bundle via a shell script unless:
#   1. QT_QPA_PLATFORM_PLUGIN_PATH points to the venv platforms dir
#   2. The process gets a window server Mach port before QApplication() loads
#      the platform plugin (handled by TransformProcessType in ui.py)
cat > "$DEST/Contents/MacOS/jarvis" <<LAUNCHER
#!/bin/bash
VENV="$REPO_DIR/.venv"
cd "$REPO_DIR"
mkdir -p "$HOME/.jarvis"
export QT_PLUGIN_PATH="$PYQT6_PLUGINS"
export QT_QPA_PLATFORM_PLUGIN_PATH="$PYQT6_PLUGINS/platforms"
echo "[jarvis] launcher start \$(date)" >> "$HOME/.jarvis/jarvis.log"
exec "$PYTHON" main.py >> "$HOME/.jarvis/jarvis.log" 2>&1
LAUNCHER
chmod +x "$DEST/Contents/MacOS/jarvis"

# ── Info.plist ────────────────────────────────────────────────────────────────
cat > "$DEST/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>      <string>jarvis</string>
    <key>CFBundleIdentifier</key>      <string>com.giljacob.jarvis-mark-xl</string>
    <key>CFBundleName</key>            <string>Jarvis Mark XL</string>
    <key>CFBundleDisplayName</key>     <string>Jarvis Mark XL</string>
    <key>CFBundleVersion</key>         <string>1.0</string>
    <key>CFBundlePackageType</key>     <string>APPL</string>
    <key>LSMinimumSystemVersion</key>  <string>12.0</string>
    <key>NSHighResolutionCapable</key> <true/>
    <key>NSPrincipalClass</key>        <string>NSApplication</string>
    <key>NSMicrophoneUsageDescription</key>
        <string>Jarvis uses the microphone for voice commands.</string>
    <key>NSCameraUsageDescription</key>
        <string>Jarvis uses the camera for face authentication (optional).</string>
</dict>
</plist>
PLIST

echo ""
echo "✅  Created: $DEST"
echo ""
echo "First launch — macOS may block unsigned apps:"
echo "  Right-click the .app → Open → Open (to approve once)"
echo ""
echo "Logs written to: ~/.jarvis/jarvis.log"
echo "Drag the app from ~/Applications to your Dock."
