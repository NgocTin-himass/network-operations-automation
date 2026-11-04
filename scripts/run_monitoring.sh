#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_DIR="/home/ngoctin/network-automation"

PYTHON="$PROJECT_DIR/.venv/bin/python"

LOG_FILE="$PROJECT_DIR/logs/cron.log"

cd "$PROJECT_DIR"

exec /usr/bin/flock -n \
    "$PROJECT_DIR/data/monitoring.lock" \
    "$PYTHON" "$PROJECT_DIR/main.py" --once \
    >> "$LOG_FILE" 2>&1
