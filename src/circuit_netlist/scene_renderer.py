from __future__ import annotations

from html import escape

from .models import Diagnostic, Severity
from .scene import SchematicScene


def render_scene_svg(scene: SchematicScene, diagnostics: list[Diagnostic] | None = None) -> str:
    width = int(scene.canvas_bounds.width)
    height = int(scene.canvas_bounds.height)
    view_box = f"{int(scene.canvas_bounds.min_x)} {int(scene.canvas_bounds.min_y)} {width} {height}"
    parts = [
        f'<svg id="schematic" xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}" data-width="{width}" data-height="{height}" data-scene-version="{escape(scene.scene_version)}">',
        '<defs><pattern id="grid-pattern" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M 20 0 L 0 0 0 20" fill="none" stroke="#28313f" stroke-width="1"/></pattern></defs>',
        f'<rect id="grid-background" width="{width}" height="{height}" fill="url(#grid-pattern)"/>',
        '<g id="wires">',
    ]
    for element in sorted(scene.elements_by_kind("net_group"), key=lambda item: item.z_index):
        parts.append(str(element.metadata.get("svg", "")))
    parts.append("</g><g id=\"components\">")
    for element in sorted(scene.elements_by_kind("component_group"), key=lambda item: item.z_index):
        parts.append(str(element.metadata.get("svg", "")))
    parts.append("</g>")
    if diagnostics:
        parts.append('<g id="diagnostic-markers">')
        for index, diag in enumerate(diagnostics):
            if diag.severity in {Severity.ERROR, Severity.FATAL, Severity.WARNING}:
                parts.append(f'<text x="24" y="{32 + index * 18}" class="svg-diagnostic">{escape(diag.severity.value)}: {escape(diag.message)}</text>')
        parts.append("</g>")
    parts.append("</svg>")
    return "".join(parts)
