#!/bin/bash
# Double-click this file in Finder to launch Jarvis Mark XL.
# macOS: System Preferences → Security → allow if blocked first run.
set -e
cd "$(dirname "$0")"

# Pick python3: venv > homebrew > system
if [ -f ".venv/bin/python3" ]; then
    PYTHON=".venv/bin/python3"
elif [ -f "/opt/homebrew/bin/python3" ]; then
    PYTHON="/opt/homebrew/bin/python3"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
else
    echo "ERROR: Python 3 not found. Install from https://brew.sh"
    read -rp "Press Enter to close…"
    exit 1
fi

echo "Launching Jarvis Mark XL…"
"$PYTHON" main.py
