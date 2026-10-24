#!/usr/bin/env sh
set -eu

# Run from the worktree root; prefer its environment over PATH or a launcher.
for candidate in ./.venv/bin/python ./.venv/Scripts/python.exe python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
        "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
        exec "$candidate" "$@"
    fi
done

if command -v py >/dev/null 2>&1 &&
    py -3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
    exec py -3 "$@"
fi

echo 'Harness requires Python 3.10+: create .venv or add Python to PATH.' >&2
exit 127
