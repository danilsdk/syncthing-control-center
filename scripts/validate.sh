#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
exec sh .githooks/run-python.sh scripts/harness.py validate "$@"
