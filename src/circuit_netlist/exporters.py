from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .models import Diagnostic
from .scene import SchematicScene
from .scene_renderer import render_scene_svg


def export_svg(svg: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")
    return path


def export_png_placeholder(svg: str, path: Path) -> Path:
    """Write a truthful placeholder until a raster backend is added."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("PNG export requires a browser canvas conversion in this version.\n", encoding="utf-8")
    return path


def export_svg_from_scene(scene: SchematicScene, path: Path, diagnostics: Sequence[Diagnostic] | None = None) -> Path:
    return export_svg(render_scene_svg(scene, list(diagnostics or [])), path)


def export_png_placeholder_from_scene(scene: SchematicScene, path: Path, diagnostics: Sequence[Diagnostic] | None = None) -> Path:
    """Write a truthful PNG placeholder whose source SVG is scene-derived."""
    path.parent.mkdir(parents=True, exist_ok=True)
    svg = render_scene_svg(scene, list(diagnostics or []))
    path.write_text(
        f"PNG export requires a browser canvas conversion in this version.\nScene version: {scene.scene_version}\nSource SVG bytes: {len(svg)}\n",
        encoding="utf-8",
    )
    return path
