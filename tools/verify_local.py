from __future__ import annotations

import subprocess
import sys


COMMANDS = [
    ["pytest", "-q"],
    [sys.executable, "-m", "pytest", "-q"],
]


def main() -> int:
    for command in COMMANDS:
        print(" ".join(command))
        completed = subprocess.run(command, check=False)
        if completed.returncode:
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
