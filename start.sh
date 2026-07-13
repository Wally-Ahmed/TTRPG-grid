#!/usr/bin/env bash
#
# start.sh — one-command bootstrap + launcher for the TTRPG Grid Map Generator
# ("Cartographer's Table").
#
# What it does, in order:
#   1. Verifies Python 3.10+ is available (clear error if not).
#   2. Creates a local virtual environment in .venv/ if one doesn't exist.
#   3. Installs the dependencies from requirements.txt the first time (or whenever
#      Flask isn't importable), then skips that step on every run afterwards.
#   4. Launches the app (python run.py), forwarding any extra arguments you pass.
#
# It is idempotent: the SAME command is both the first-run installer and the
# everyday launcher. After the first run, startup takes well under a second
# because setup is skipped.
#
# Usage:
#   ./start.sh                       # set up (first run) then serve on http://127.0.0.1:5000
#   ./start.sh --port 8080           # pass through to run.py (custom port)
#   ./start.sh --no-browser          # don't auto-open a browser tab
#   PORT=8080 ./start.sh             # env-var port also works (read by run.py)
#
set -euo pipefail

# Always operate from the directory this script lives in, so it works no matter
# where it's invoked from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR=".venv"
REQUIREMENTS="requirements.txt"

# --- 1. Locate a suitable Python interpreter (need >= 3.10) -------------------
PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      PYTHON="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  echo "ERROR: Python 3.10 or newer is required but was not found." >&2
  echo "" >&2
  echo "  Install Python 3.10+ and make sure 'python3' is on your PATH, then" >&2
  echo "  run ./start.sh again." >&2
  echo "" >&2
  echo "  - macOS:   brew install python   (or download from https://www.python.org/downloads/)" >&2
  echo "  - Linux:   use your package manager, e.g. 'sudo apt install python3 python3-venv'" >&2
  echo "  - Windows: use start.bat instead of this script." >&2
  exit 1
fi

# --- 2. Create the virtual environment if it doesn't exist -------------------
if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtual environment in $VENV_DIR/ ..."
  "$PYTHON" -m venv "$VENV_DIR"
fi

# Path to the interpreter inside the venv (POSIX layout: bin/, Windows: Scripts/).
if [ -x "$VENV_DIR/bin/python" ]; then
  VENV_PY="$VENV_DIR/bin/python"
elif [ -x "$VENV_DIR/Scripts/python.exe" ]; then
  VENV_PY="$VENV_DIR/Scripts/python.exe"
else
  echo "ERROR: virtual environment looks broken (no python inside $VENV_DIR)." >&2
  echo "  Delete the $VENV_DIR/ folder and run ./start.sh again to recreate it." >&2
  exit 1
fi

# --- 3. Install dependencies only when needed --------------------------------
# We probe for Flask specifically: if it imports, setup is already done and we
# skip the (slow) pip step entirely on subsequent launches.
if ! "$VENV_PY" -c 'import flask' >/dev/null 2>&1; then
  echo "Installing dependencies (first run only) ..."
  "$VENV_PY" -m pip install --upgrade pip >/dev/null
  "$VENV_PY" -m pip install -r "$REQUIREMENTS"
  echo "Dependencies installed."
fi

# --- 4. Launch the app, forwarding any extra arguments -----------------------
exec "$VENV_PY" run.py "$@"
