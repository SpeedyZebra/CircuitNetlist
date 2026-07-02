from __future__ import annotations

from pathlib import Path


def export_svg(svg: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")
    return path


def export_png_placeholder(svg: str, path: Path) -> Path:
    """Write a truthful placeholder until a raster backend is added."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("PNG export requires a browser canvas conversion in this version.\n", encoding="utf-8")
    return path
