#!/usr/bin/env bash
# Launch JARVIS MARK XL using the project virtual environment.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "[MARK XL] Creating .venv…"
  python3 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" install --quiet --disable-pip-version-check \
    -r "$ROOT/requirements.txt" 2>/dev/null || true
fi

exec "$ROOT/.venv/bin/python" "$ROOT/main.py" "$@"