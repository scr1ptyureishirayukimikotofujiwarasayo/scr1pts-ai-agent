#!/usr/bin/env sh
# Launch the agent on macOS / Linux / WSL.
set -e
cd "$(dirname "$0")"
if [ -x ".venv/bin/python" ]; then
  exec .venv/bin/python run.py "$@"
fi
exec python3 run.py "$@"
