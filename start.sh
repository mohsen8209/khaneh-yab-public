#!/usr/bin/env bash
set -euo pipefail

# Bootstrap-safe Chabokan defaults. Panel variables override every value here.
export KHANEYAB_RUNTIME="${KHANEYAB_RUNTIME:-paas}"
export KHANEYAB_HOST="${KHANEYAB_HOST:-0.0.0.0}"
export KHANEYAB_PORT="${KHANEYAB_PORT:-8766}"
export KHANEYAB_DATA_DIR="${KHANEYAB_DATA_DIR:-/app/runtime-data}"
export KHANEYAB_WATCH_ENABLED="${KHANEYAB_WATCH_ENABLED:-false}"
export KHANEYAB_ALLOW_EMPTY_DATABASE="${KHANEYAB_ALLOW_EMPTY_DATABASE:-false}"

exec python app.py
