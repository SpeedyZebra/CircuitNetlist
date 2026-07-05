from __future__ import annotations

from typing import Any

from .geometry import boxes_overlap, inflate_box, segment_crosses_box, segments_collinear_overlap
from .models import Circuit, Diagnostic, Layout, Severity
from .scene import SchematicScene
from .scene_builder import build_schematic_scene


WireLikeSegment = tuple[str, str, str, tuple[int, int, int, int], str, str]


def visual_drc(circuit: Circuit, library: Any, layout: Layout, routes: Any) -> tuple[list[Diagnostic], dict[str, Any]]:
    scene = build_schematic_scene(circuit, library, layout, list(routes))
    return visual_drc_from_scene(scene)


def visual_drc_from_scene(scene: SchematicScene) -> tuple[list[Diagnostic], dict[str, Any]]:
    diagnostics: list[Diagnostic] = []
    body_boxes: list[tuple[str, tuple[float, float, float, float]]] = []
    symbol_boxes: list[tuple[str, tuple[float, float, float, float]]] = []
    text_boxes: list[tuple[str, tuple[float, float, float, float]]] = []
    pin_points: dict[str, set[tuple[int, int]]] = {}
    physical_text_boxes: list[tuple[str, tuple[float, float, float, float]]] = []
    label_text_boxes: list[tuple[str, str, str, str, tuple[float, float, float, float]]] = []
    label_shape_boxes: list[tuple[str, str, str, str, tuple[float, float, float, float]]] = []
    power_symbol_boxes: list[tuple[str, str, str, str, tuple[float, float, float, float]]] = []

    for element in scene.elements:
        if element.kind == "component_body" and element.component_ref:
            body_boxes.append((element.component_ref, _element_rect_box(element) or element.active_collision_bounds().as_tuple()))
        if element.kind == "component_symbol" and element.component_ref:
            symbol_boxes.append((element.component_ref, element.active_collision_bounds().as_tuple()))
        if element.kind == "pin" and element.component_ref:
            point = _pin_center(element)
            pin_points.setdefault(element.component_ref, set()).add(point)
        if element.text and element.metadata.get("drc_wire_text", True):
            item = (element.component_ref or "", element.text.bounds.as_tuple())
            text_boxes.append(item)
            if element.metadata.get("physical_wire_text_drc"):
                physical_text_boxes.append(item)
            if element.kind in {"net_label", "power_label", "ground_label"}:
                label_text_boxes.append((element.id, element.parent_id or "", element.net_name or "", element.kind, element.text.bounds.as_tuple()))
        if element.kind == "net_label_endpoint":
            label_shape_boxes.append((element.id, element.parent_id or "", element.net_name or "", element.kind, element.active_collision_bounds().as_tuple()))
        if element.kind in {"power_symbol", "ground_symbol"}:
            power_symbol_boxes.append((element.id, element.parent_id or "", element.net_name or "", element.kind, element.active_collision_bounds().as_tuple()))

    wire_like = wire_like_segments_from_scene(scene)
    component_overlaps = sum(1 for i, (_, a) in enumerate(body_boxes) for _, b in body_boxes[i + 1 :] if boxes_overlap(a, b))

    wire_symbol_overlaps = 0
    stub_symbol_overlaps = 0
    for _, kind, source_ref, segment, *_ in wire_like:
        for ref, box in symbol_boxes:
            if ref == source_ref or (segment[0], segment[1]) in pin_points.get(ref, set()) or (segment[2], segment[3]) in pin_points.get(ref, set()):
                continue
            if segment_crosses_box(segment, box):
                wire_symbol_overlaps += 1
                if kind != "wire":
                    stub_symbol_overlaps += 1

    wire_text_overlaps = sum(
        1
        for _, _, _, segment, *_ in wire_like
        for ref, box in text_boxes
        if not _segment_touches_component_pin(segment, pin_points.get(ref, set())) and segment_crosses_box(segment, inflate_box(box, 8))
    )
    physical_wire_text_overlaps = sum(
        1
        for _, kind, _, segment, *_ in wire_like
        if kind == "wire"
        for ref, box in physical_text_boxes
        if not _segment_touches_component_pin(segment, pin_points.get(ref, set())) and segment_crosses_box(segment, inflate_box(box, 8))
    )
    visible_wire_text_overlaps = sum(
        1
        for _, kind, _, segment, _, parent_id in wire_like
        if kind == "wire"
        for _, text_parent_id, _, _, box in label_text_boxes
        if not _own_attachment_contact(segment, parent_id, text_parent_id, box) and segment_crosses_box(segment, inflate_box(box, 4))
    )
    wire_label_overlaps = sum(
        1
        for _, kind, _, segment, _, parent_id in wire_like
        if kind == "wire"
        for _, label_parent_id, _, _, box in label_shape_boxes
        if not _own_attachment_contact(segment, parent_id, label_parent_id, box) and segment_crosses_box(segment, box)
    )
    stub_label_overlaps = sum(
        1
        for _, kind, _, segment, _, parent_id in wire_like
        if kind != "wire"
        for _, label_parent_id, _, _, box in [*label_shape_boxes, *label_text_boxes]
        if not _own_attachment_contact(segment, parent_id, label_parent_id, box) and segment_crosses_box(segment, inflate_box(box, 3))
    )
    power_symbol_overlaps = sum(
        1
        for _, _, _, segment, _, parent_id in wire_like
        for _, symbol_parent_id, _, _, box in power_symbol_boxes
        if not _own_attachment_contact(segment, parent_id, symbol_parent_id, box) and segment_crosses_box(segment, box)
    )
    label_label_overlaps = sum(
        1
        for index, a in enumerate(label_text_boxes)
        for b in label_text_boxes[index + 1 :]
        if a[1] != b[1] and boxes_overlap(a[4], b[4])
    )
    wire_overlaps = sum(1 for i, a in enumerate(wire_like) for b in wire_like[i + 1 :] if a[0] != b[0] and segments_collinear_overlap(a[3], b[3]))

    if component_overlaps:
        diagnostics.append(Diagnostic(severity=Severity.ERROR, code="DRC_COMPONENT_OVERLAP", message=f"{component_overlaps} component body overlaps"))
    if wire_symbol_overlaps:
        diagnostics.append(Diagnostic(severity=Severity.ERROR, code="DRC_WIRE_SYMBOL_OVERLAP", message=f"{wire_symbol_overlaps} wire/symbol overlaps"))
    if stub_symbol_overlaps:
        diagnostics.append(Diagnostic(severity=Severity.ERROR, code="DRC_STUB_SYMBOL_OVERLAP", message=f"{stub_symbol_overlaps} stub/symbol overlaps"))
    if wire_label_overlaps:
        diagnostics.append(Diagnostic(severity=Severity.ERROR, code="DRC_WIRE_LABEL_OVERLAP", message=f"{wire_label_overlaps} wire/label endpoint overlaps"))
    if stub_label_overlaps:
        diagnostics.append(Diagnostic(severity=Severity.WARNING, code="DRC_STUB_LABEL_OVERLAP", message=f"{stub_label_overlaps} stub/label overlaps"))
    if power_symbol_overlaps:
        diagnostics.append(Diagnostic(severity=Severity.ERROR, code="DRC_POWER_SYMBOL_OVERLAP", message=f"{power_symbol_overlaps} wire or stub/power-symbol overlaps"))
    if label_label_overlaps:
        diagnostics.append(Diagnostic(severity=Severity.WARNING, code="DRC_LABEL_LABEL_OVERLAP", message=f"{label_label_overlaps} label/label overlaps"))
    if wire_overlaps:
        diagnostics.append(Diagnostic(severity=Severity.WARNING, code="DRC_WIRE_WIRE_OVERLAP", message=f"{wire_overlaps} unrelated wire overlaps"))

    total_wire_text_overlaps = physical_wire_text_overlaps + visible_wire_text_overlaps
    if total_wire_text_overlaps:
        diagnostics.append(Diagnostic(severity=Severity.WARNING, code="DRC_WIRE_TEXT_OVERLAP", message=f"{total_wire_text_overlaps} wire/text overlaps"))

    total_wire_length = sum(abs(x1 - x2) + abs(y1 - y2) for _, _, _, (x1, y1, x2, y2), *_ in wire_like)
    metrics = {
        "component_body_overlaps": component_overlaps,
        "wire_symbol_overlaps": wire_symbol_overlaps,
        "stub_symbol_overlaps": stub_symbol_overlaps,
        "wire_text_overlaps": wire_text_overlaps,
        "physical_wire_text_overlaps": physical_wire_text_overlaps,
        "visible_wire_text_overlaps": visible_wire_text_overlaps,
        "wire_label_overlaps": wire_label_overlaps,
        "stub_label_overlaps": stub_label_overlaps,
        "power_symbol_overlaps": power_symbol_overlaps,
        "label_label_overlaps": label_label_overlaps,
        "unrelated_wire_overlaps": wire_overlaps,
        "total_wire_length": total_wire_length,
        "physical_wire_segments": sum(1 for _, kind, _, _, *_ in wire_like if kind == "wire"),
        "net_labels": sum(1 for element in scene.elements if element.kind == "net_label" and element.metadata.get("attachment")),
        "power_symbols": sum(1 for element in scene.elements if element.kind in {"power_symbol", "ground_symbol"}),
        "orientations": scene.metadata.get("orientations", {}),
    }
    return diagnostics, metrics


def wire_like_segments(circuit: Circuit, library: Any, layout: Layout, routes: Any) -> list[WireLikeSegment]:
    return wire_like_segments_from_scene(build_schematic_scene(circuit, library, layout, list(routes)))


def wire_like_segments_from_scene(scene: SchematicScene) -> list[WireLikeSegment]:
    segments: list[WireLikeSegment] = []
    for element in scene.elements:
        if element.kind not in {"wire", "wire_stub"}:
            continue
        segment = line_segment_from_element(element)
        if not segment or len(segment) != 4:
            continue
        segments.append(
            (
                element.net_name or "",
                str(element.metadata.get("wire_kind", "wire")),
                str(element.metadata.get("source_ref", "")),
                segment,
                element.id,
                element.parent_id or "",
            )
        )
    return segments


def line_segment_from_element(element: Any) -> tuple[int, int, int, int] | None:
    primitive = next((primitive for primitive in element.primitives if primitive.kind == "line"), None)
    if primitive:
        geometry = primitive.geometry
        return (round(float(geometry["x1"])), round(float(geometry["y1"])), round(float(geometry["x2"])), round(float(geometry["y2"])))
    segment = element.metadata.get("segment")
    if segment and len(segment) == 4:
        return (int(segment[0]), int(segment[1]), int(segment[2]), int(segment[3]))
    return None


def element_rect_box(element: Any) -> tuple[float, float, float, float] | None:
    primitive = next((primitive for primitive in element.primitives if primitive.kind == "rect"), None)
    if primitive is None:
        return None
    geometry = primitive.geometry
    x = float(geometry["x"])
    y = float(geometry["y"])
    return (x, y, x + float(geometry["width"]), y + float(geometry["height"]))


def pin_center(element: Any) -> tuple[int, int]:
    primitive = element.primitives[0].geometry if element.primitives else {}
    if "cx" in primitive and "cy" in primitive:
        return (round(float(primitive["cx"])), round(float(primitive["cy"])))
    return (round((element.bounds.min_x + element.bounds.max_x) / 2), round((element.bounds.min_y + element.bounds.max_y) / 2))


def _element_rect_box(element: Any) -> tuple[float, float, float, float] | None:
    return element_rect_box(element)


def _pin_center(element: Any) -> tuple[int, int]:
    return pin_center(element)


def _segment_touches_component_pin(segment: tuple[int, int, int, int], pins: set[tuple[int, int]]) -> bool:
    return (segment[0], segment[1]) in pins or (segment[2], segment[3]) in pins


def _own_attachment_contact(segment: tuple[int, int, int, int], parent_id: str, target_parent_id: str, box: tuple[float, float, float, float]) -> bool:
    return bool(parent_id and parent_id == target_parent_id and _segment_endpoint_touches_box(segment, inflate_box(box, 2)))


def _segment_endpoint_touches_box(segment: tuple[int, int, int, int], box: tuple[float, float, float, float]) -> bool:
    x1, y1, x2, y2 = segment
    return _point_in_box((x1, y1), box) or _point_in_box((x2, y2), box)


def _point_in_box(point: tuple[int, int], box: tuple[float, float, float, float]) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]
