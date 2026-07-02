from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "dist" / "CircuitNetlist-source.zip"
sys.path.insert(0, str(ROOT / "src"))

from circuit_netlist.source_archive import create_archive  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a clean source ZIP for CircuitNetlist.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Archive path to write.")
    args = parser.parse_args()
    path = create_archive(ROOT, args.output)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
