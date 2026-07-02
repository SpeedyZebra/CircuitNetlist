from __future__ import annotations

import zipfile
from pathlib import Path


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


def create_archive(root: Path, output: Path) -> Path:
    root = root.resolve()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if path == output or not should_include(relative):
                continue
            archive.write(path, relative.as_posix())
    return output
