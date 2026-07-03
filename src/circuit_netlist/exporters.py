from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Sequence

from .models import Diagnostic
from .scene import RenderPrimitive, SchematicScene
from .scene_renderer import render_scene_svg


def export_svg(svg: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")
    return path


def export_png_placeholder(svg: str, path: Path) -> Path:
    raise RuntimeError("PNG export now requires canonical scene export. Use export_png_from_scene instead.")


def export_svg_from_scene(scene: SchematicScene, path: Path, diagnostics: Sequence[Diagnostic] | None = None) -> Path:
    return export_svg(render_scene_svg(scene, list(diagnostics or [])), path)


def export_png_from_scene(scene: SchematicScene, path: Path, diagnostics: Sequence[Diagnostic] | None = None) -> Path:
    """Rasterize scene primitives to a real PNG after generating the canonical SVG."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(export_png_bytes_from_scene(scene, diagnostics))
    return path


def export_png_bytes_from_scene(scene: SchematicScene, diagnostics: Sequence[Diagnostic] | None = None) -> bytes:
    render_scene_svg(scene, list(diagnostics or []))
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("PNG export requires the pillow package. Install project dependencies and try again.") from exc

    width = int(scene.canvas_bounds.width)
    height = int(scene.canvas_bounds.height)
    image = Image.new("RGBA", (width, height), (13, 18, 24, 255))
    draw = ImageDraw.Draw(image)
    for x in range(0, width, 20):
        draw.line([(x, 0), (x, height)], fill=(40, 49, 63, 255), width=1)
    for y in range(0, height, 20):
        draw.line([(0, y), (width, y)], fill=(40, 49, 63, 255), width=1)
    font = ImageFont.load_default()
    for element in sorted((item for item in scene.elements if item.visible), key=lambda item: (item.layer != "wires", item.z_index)):
        for primitive in element.primitives:
            if primitive.visible:
                _draw_primitive(draw, primitive, font)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


export_png_placeholder_from_scene = export_png_from_scene


def _draw_primitive(draw: object, primitive: RenderPrimitive, font: object) -> None:
    geometry = primitive.geometry
    css_class = primitive.style_class or ""
    stroke = _stroke(css_class)
    fill = _fill(css_class)
    width = _stroke_width(css_class)
    if primitive.kind == "line":
        draw.line([(geometry["x1"], geometry["y1"]), (geometry["x2"], geometry["y2"])], fill=stroke, width=width)
    elif primitive.kind == "rect":
        xy = [geometry["x"], geometry["y"], geometry["x"] + geometry["width"], geometry["y"] + geometry["height"]]
        draw.rounded_rectangle(xy, radius=int(geometry.get("rx", 0)), fill=fill, outline=stroke, width=max(1, min(width, 3)))
    elif primitive.kind == "circle":
        cx = geometry["cx"]
        cy = geometry["cy"]
        r = geometry["r"]
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill if fill[3] else stroke, outline=stroke, width=max(1, min(width, 3)))
    elif primitive.kind == "polyline":
        draw.line(geometry["points"], fill=stroke, width=width, joint="curve")
    elif primitive.kind == "polygon":
        draw.polygon(geometry["points"], fill=fill, outline=stroke)
    elif primitive.kind == "path":
        _draw_path(draw, geometry["d"], geometry.get("translate", (0, 0)), stroke, fill, width)
    elif primitive.kind == "text":
        draw.text((geometry["x"], geometry["y"] - 12), str(geometry.get("text", "")), fill=fill if fill[3] else (53, 200, 255, 255), font=font)


def _draw_path(draw: object, d: str, translate: tuple[float, float], stroke: tuple[int, int, int, int], fill: tuple[int, int, int, int], width: int) -> None:
    tokens = re.findall(r"[A-Za-z]|-?\d+(?:\.\d+)?", d)
    x = y = 0.0
    start: tuple[float, float] | None = None
    points: list[tuple[float, float]] = []
    command = ""
    index = 0
    tx, ty = translate
    while index < len(tokens):
        token = tokens[index]
        if re.match(r"[A-Za-z]", token):
            command = token
            index += 1
            if command in {"Z", "z"} and start:
                points.append(start)
            continue
        if command in {"M", "L"}:
            x = float(token)
            y = float(tokens[index + 1])
            point = (x + tx, y + ty)
            points.append(point)
            start = start or point
            index += 2
        elif command in {"m", "l"}:
            x += float(token)
            y += float(tokens[index + 1])
            point = (x + tx, y + ty)
            points.append(point)
            start = start or point
            index += 2
        elif command in {"H", "h"}:
            x = x + float(token) if command == "h" else float(token)
            points.append((x + tx, y + ty))
            index += 1
        elif command in {"V", "v"}:
            y = y + float(token) if command == "v" else float(token)
            points.append((x + tx, y + ty))
            index += 1
        else:
            index += 1
    if len(points) > 2 and fill[3]:
        draw.polygon(points, fill=fill, outline=stroke)
    elif len(points) > 1:
        draw.line(points, fill=stroke, width=width)


def _stroke(css_class: str) -> tuple[int, int, int, int]:
    if "wire" in css_class:
        return (183, 196, 210, 255)
    if "power" in css_class:
        return (134, 224, 255, 255)
    if "ground" in css_class:
        return (158, 231, 173, 255)
    if "symbol" in css_class:
        return (24, 32, 42, 255)
    return (24, 32, 42, 255)


def _fill(css_class: str) -> tuple[int, int, int, int]:
    if "body" in css_class:
        return (207, 214, 223, 255)
    if "symbol-fill" in css_class or "power-flag" in css_class or "label-flag" in css_class:
        return (223, 243, 223, 255)
    if "pin" in css_class:
        return (24, 32, 42, 255)
    if "timer" in css_class or "label" in css_class or "polarity" in css_class:
        return (53, 200, 255, 255)
    return (0, 0, 0, 0)


def _stroke_width(css_class: str) -> int:
    if "wire" in css_class:
        return 3
    if "power" in css_class:
        return 3
    return 2
