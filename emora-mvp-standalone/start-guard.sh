#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$APP_DIR/.venv/bin/python" -u "$APP_DIR/scripts/launch.py" --guard "$@"
