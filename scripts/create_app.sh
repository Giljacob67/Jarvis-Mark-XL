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

echo "→ Repo:   $REPO_DIR"
echo "→ Python: $PYTHON"
echo "→ App:    $DEST"

mkdir -p "$HOME/Applications"
rm -rf "$DEST"
mkdir -p "$DEST/Contents/MacOS"
mkdir -p "$DEST/Contents/Resources"

# ── Launcher script ───────────────────────────────────────────────────────────
cat > "$DEST/Contents/MacOS/jarvis" <<LAUNCHER
#!/bin/bash
cd "$REPO_DIR"
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
