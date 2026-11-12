"""Launch the installed npm/npx executable on Windows and POSIX systems."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", choices=("npm", "npx"))
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    executable = shutil.which(options.tool)
    if executable is None:
        print(f"{options.tool} not found on PATH", file=sys.stderr)
        return 127
    try:
        result = subprocess.run(
            [executable, *options.arguments],
            cwd=Path(__file__).resolve().parents[1],
            check=False,
        )
    except OSError as error:
        print(f"Could not start {options.tool} (OS error {error.errno}).", file=sys.stderr)
        return 127
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
