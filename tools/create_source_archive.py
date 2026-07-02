from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "dist" / "CircuitNetlist-source.zip"

EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "env",
    "htmlcov",
    "output",
    "venv",
}
EXCLUDED_PREFIXES = {
    ("test_circuits", "generated"),
}
EXCLUDED_SUFFIXES = {
    ".egg-info",
}
EXCLUDED_FILES = {
    ".coverage",
    "coverage.xml",
}


def should_include(path: Path) -> bool:
    parts = path.parts
    if not parts:
        return False
    if any(part in EXCLUDED_DIRS for part in parts):
        return False
    if any(tuple(parts[: len(prefix)]) == prefix for prefix in EXCLUDED_PREFIXES):
        return False
    if any(part.endswith(suffix) for part in parts for suffix in EXCLUDED_SUFFIXES):
        return False
    if parts[-1] in EXCLUDED_FILES:
        return False
    if parts[-1].endswith((".pyc", ".pyo", ".zip")):
        return False
    return True


def create_archive(output: Path = DEFAULT_OUTPUT) -> Path:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(ROOT)
            if relative == output.relative_to(ROOT) or not should_include(relative):
                continue
            archive.write(path, relative.as_posix())
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a clean source ZIP for CircuitNetlist.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Archive path to write.")
    args = parser.parse_args()
    path = create_archive(args.output)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
