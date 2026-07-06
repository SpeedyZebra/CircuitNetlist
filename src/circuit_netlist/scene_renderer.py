from __future__ import annotations

from html import escape
from typing import Any

from .models import Diagnostic, Severity
from .scene import RenderPrimitive, SceneElement, SchematicScene
from .schematic_geometry import safe_id


def render_scene_svg(scene: SchematicScene, diagnostics: list[Diagnostic] | None = None) -> str:
    width = int(scene.canvas_bounds.width)
    height = int(scene.canvas_bounds.height)
    view_box = f"{int(scene.canvas_bounds.min_x)} {int(scene.canvas_bounds.min_y)} {width} {height}"
    by_parent = _children_by_parent(scene)
    parts = [
        f'<svg id="schematic" xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}" data-width="{width}" data-height="{height}" data-scene-version="{escape(scene.scene_version)}">',
        '<defs><pattern id="grid-pattern" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M 20 0 L 0 0 0 20" fill="none" stroke="#28313f" stroke-width="1"/></pattern></defs>',
        f'<rect id="grid-background" width="{width}" height="{height}" fill="url(#grid-pattern)"/>',
        '<g id="wires">',
    ]
    for group in sorted(scene.elements_by_kind("net_group"), key=lambda item: item.z_index):
        parts.append(_render_group(group, by_parent.get(group.id, [])))
    parts.append("</g><g id=\"components\">")
    for group in sorted(scene.elements_by_kind("component_group"), key=lambda item: item.z_index):
        parts.append(_render_group(group, by_parent.get(group.id, [])))
    parts.append("</g>")
    if diagnostics:
        parts.append('<g id="diagnostic-markers">')
        for index, diag in enumerate(diagnostics):
            if diag.severity in {Severity.ERROR, Severity.FATAL, Severity.WARNING}:
                parts.append(f'<text x="24" y="{32 + index * 18}" class="svg-diagnostic">{escape(diag.severity.value)}: {escape(diag.message)}</text>')
        parts.append("</g>")
    parts.append("</svg>")
    return "".join(parts)


def _children_by_parent(scene: SchematicScene) -> dict[str, list[SceneElement]]:
    result: dict[str, list[SceneElement]] = {}
    for element in scene.elements:
        if element.parent_id:
            result.setdefault(element.parent_id, []).append(element)
    return result


def _render_group(group: SceneElement, children: list[SceneElement]) -> str:
    if group.kind == "component_group":
        attrs = {
            "id": group.id,
            "class": "component",
            "data-ref": group.component_ref,
            "data-component-ref": group.component_ref,
            "data-component-id": group.component_id,
            "data-category": group.metadata.get("category"),
            "data-orientation": "vertical" if int(group.metadata.get("rotation", 0)) % 180 == 90 else "horizontal",
            "data-placement-x": group.metadata.get("placement", {}).get("x"),
            "data-placement-y": group.metadata.get("placement", {}).get("y"),
            "data-selectable": str(group.selectable).lower(),
        }
        parts = [f"<g{_attrs(group, attrs)}>"]
        hit = group.active_hit_bounds()
        parts.append(
            f'<rect class="hit-area" data-owner-id="{escape(group.id)}" data-kind="component_group" data-selectable="{str(group.selectable).lower()}" '
            f'data-ref="{escape(str(group.component_ref or ""))}" data-component-ref="{escape(str(group.component_ref or ""))}" '
            f'data-component-id="{escape(str(group.component_id or ""))}" x="{_num(hit.min_x)}" y="{_num(hit.min_y)}" width="{_num(hit.width)}" height="{_num(hit.height)}"/>'
        )
        for child in sorted(children, key=lambda item: item.z_index):
            parts.append(_render_element(child))
        parts.append("</g>")
        return "".join(parts)
    attrs = {
        "id": group.id,
        "class": "net",
        "data-net": group.net_name,
        "data-render-style": group.metadata.get("render_style"),
    }
    parts = [f"<g{_attrs(group, attrs)}>"]
    for child in sorted(children, key=lambda item: item.z_index):
        parts.append(_render_element(child))
    parts.append("</g>")
    return "".join(parts)


def _render_element(element: SceneElement) -> str:
    if not element.visible:
        return ""
    attrs = _element_attrs(element)
    if len(element.primitives) > 1 or element.kind in {"component_symbol", "pin", "net_label_endpoint", "power_symbol", "ground_symbol"}:
        group_attrs = dict(attrs)
        group_attrs.setdefault("id", _dom_id(element))
        group_attrs.setdefault("class", _element_class(element))
        if element.kind == "component_symbol" and element.metadata.get("renderer") in {"nmos", "pmos"}:
            group_attrs["data-symbol-style"] = "compact_no_bulk"
            group_attrs["data-mosfet-mode"] = _first_metadata(element, "mosfet_mode", "enhancement")
        return f"<g{_attrs(element, group_attrs)}>{''.join(_render_primitive(element, primitive, include_semantic_attrs=False) for primitive in element.primitives if primitive.visible)}</g>"
    if not element.primitives and element.text:
        primitive = RenderPrimitive(kind="text", style_class=_element_class(element), geometry={"x": element.text.origin.x, "y": element.text.origin.y, "anchor": element.text.anchor, "text": element.text.text})
        return _render_primitive(element, primitive, include_semantic_attrs=True)
    return "".join(_render_primitive(element, primitive, include_semantic_attrs=True) for primitive in element.primitives if primitive.visible)


def _render_primitive(element: SceneElement, primitive: RenderPrimitive, include_semantic_attrs: bool) -> str:
    attrs = _primitive_attrs(element, primitive, include_semantic_attrs)
    geometry = primitive.geometry
    if primitive.kind == "line":
        attrs.update({"x1": geometry["x1"], "y1": geometry["y1"], "x2": geometry["x2"], "y2": geometry["y2"]})
        return f"<line{_attrs(element, attrs, include_semantic_attrs)}/>"
    if primitive.kind == "rect":
        attrs.update({"x": geometry["x"], "y": geometry["y"], "width": geometry["width"], "height": geometry["height"]})
        if "rx" in geometry:
            attrs["rx"] = geometry["rx"]
        return f"<rect{_attrs(element, attrs, include_semantic_attrs)}/>"
    if primitive.kind == "circle":
        attrs.update({"cx": geometry["cx"], "cy": geometry["cy"], "r": geometry["r"]})
        return f"<circle{_attrs(element, attrs, include_semantic_attrs)}/>"
    if primitive.kind in {"polyline", "polygon"}:
        attrs["points"] = " ".join(f"{_num(x)},{_num(y)}" for x, y in geometry["points"])
        return f"<{primitive.kind}{_attrs(element, attrs, include_semantic_attrs)}/>"
    if primitive.kind == "path":
        attrs["d"] = geometry["d"]
        if "translate" in geometry:
            tx, ty = geometry["translate"]
            attrs["transform"] = f"translate({_num(tx)},{_num(ty)})"
        return f"<path{_attrs(element, attrs, include_semantic_attrs)}/>"
    if primitive.kind == "text":
        attrs.update({"x": geometry["x"], "y": geometry["y"]})
        anchor = geometry.get("anchor")
        if anchor:
            attrs["text-anchor"] = anchor
        text_value = str(geometry.get("text") if geometry.get("text") is not None else (element.text.text if element.text else ""))
        return f"<text{_attrs(element, attrs, include_semantic_attrs)}>{escape(text_value)}</text>"
    return ""


def _primitive_attrs(element: SceneElement, primitive: RenderPrimitive, include_semantic_attrs: bool) -> dict[str, Any]:
    attrs: dict[str, Any] = {"class": primitive.style_class or _element_class(element)}
    if include_semantic_attrs:
        attrs.update(_element_attrs(element))
    if primitive.id:
        attrs["id"] = primitive.id
    elif include_semantic_attrs:
        attrs.setdefault("id", _dom_id(element))
    for key, value in {
        "stroke": primitive.stroke,
        "stroke-width": primitive.stroke_width,
        "fill": primitive.fill,
        "stroke-linecap": primitive.line_cap,
        "stroke-linejoin": primitive.line_join,
        "stroke-dasharray": primitive.dash_pattern,
    }.items():
        if value is not None:
            attrs[key] = value
    if primitive.kind == "text":
        for key, value in primitive.text_style.items():
            attrs[key.replace("_", "-")] = value
    return attrs


def _element_attrs(element: SceneElement) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "id": _dom_id(element),
        "data-kind": element.kind,
        "data-scene-id": element.id,
        "data-owner-id": element.owner_id or element.parent_id,
        "data-layer": element.layer,
        "data-selectable": str(element.selectable).lower(),
    }
    if element.component_ref:
        attrs["data-ref"] = element.component_ref
        attrs["data-component-ref"] = element.component_ref
    if element.component_id:
        attrs["data-component-id"] = element.component_id
    if element.pin_number:
        attrs["data-pin-number"] = element.pin_number
    if element.pin_name:
        attrs["data-pin-name"] = element.pin_name
    if element.net_name:
        attrs["data-net"] = element.net_name
        attrs["data-net-name"] = element.net_name
    electrical_type = element.metadata.get("electrical_type")
    if electrical_type:
        attrs["data-electrical-type"] = electrical_type
    render_style = element.metadata.get("render_style")
    if render_style:
        attrs["data-render-style"] = render_style
    return attrs


def _element_class(element: SceneElement) -> str:
    return {
        "component_body": "body",
        "component_visible_body": "body",
        "component_symbol": "symbol",
        "pin": "pin",
        "wire": "wire",
        "wire_stub": "wire",
        "junction": "junction",
        "net_label_endpoint": "net-label-endpoint",
        "net_label": "net-label",
        "power_symbol": "power-symbol",
        "ground_symbol": "power-symbol ground-symbol",
        "power_label": "net-label power-label",
        "ground_label": "net-label ground-label",
        "component_reference_text": "ref-label",
        "component_value_text": "value-label",
        "pin_number_text": "pin-number",
        "pin_name_text": "pin-name",
    }.get(element.kind, element.kind)


def _dom_id(element: SceneElement) -> str:
    if element.kind == "pin" and element.component_ref and element.pin_number:
        return f"pin-{safe_id(element.component_ref)}-{safe_id(element.pin_number)}"
    if element.kind == "wire":
        svg_id = element.metadata.get("svg_id")
        if svg_id:
            return str(svg_id)
    if element.kind in {"power_symbol", "ground_symbol"}:
        return element.id.removesuffix(":symbol")
    return safe_id(element.id.replace(":", "-"))


def _attrs(element: SceneElement, attrs: dict[str, Any], include_scene_defaults: bool = True) -> str:
    if include_scene_defaults:
        attrs.setdefault("data-scene-id", element.id)
        attrs.setdefault("data-kind", element.kind)
    parts = []
    for key, value in attrs.items():
        if value is None:
            continue
        parts.append(f' {key}="{escape(_num(value) if isinstance(value, (int, float)) else str(value))}"')
    return "".join(parts)


def _num(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _first_metadata(element: SceneElement, key: str, default: str) -> str:
    for primitive in element.primitives:
        if key in primitive.metadata:
            return str(primitive.metadata[key])
    return default
