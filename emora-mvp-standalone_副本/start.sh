#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  printf 'Missing project environment. Run `uv sync` first.\n' >&2
  exit 1
fi
exec "$APP_DIR/.venv/bin/python" -u "$APP_DIR/scripts/start_all.py" "$@"
