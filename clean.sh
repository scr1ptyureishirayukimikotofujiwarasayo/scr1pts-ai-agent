#!/usr/bin/env sh
# Remove API keys, sessions and caches before sharing the harness.
# Usage: ./clean.sh [-y] [--dry-run] [--keep-config] [--keep-mcp]
set -eu
cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
else
    PY="${PYTHON:-python}"
fi

if ! command -v "$PY" >/dev/null 2>&1; then
    echo "Python was not found. Install Python 3.10+ or create .venv first." >&2
    exit 1
fi

exec "$PY" -m agent.cleanup "$@"
