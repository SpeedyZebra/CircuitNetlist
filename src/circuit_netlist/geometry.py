from __future__ import annotations

from .models import ComponentDefinition, PinDefinition, Placement

Box = tuple[float, float, float, float]
Segment = tuple[int, int, int, int]

MIN_COMPONENT_CLEARANCE = 16
MIN_WIRE_TO_SYMBOL_CLEARANCE = 10
MIN_WIRE_TO_TEXT_CLEARANCE = 8
MIN_TEXT_TO_TEXT_CLEARANCE = 6
MIN_WIRE_TO_WIRE_CLEARANCE = 6

ORIENTABLE_RENDERERS = {"resistor", "capacitor", "polarized_capacitor", "inductor"}


def is_orientation_sensitive(definition: ComponentDefinition) -> bool:
    return len(definition.pins) == 2 and definition.body.renderer in ORIENTABLE_RENDERERS


def is_vertical(definition: ComponentDefinition, placement: Placement | None) -> bool:
    return bool(placement and is_orientation_sensitive(definition) and placement.rotation % 180 == 90)


def component_size(definition: ComponentDefinition, placement: Placement | None) -> tuple[int, int]:
    if is_vertical(definition, placement):
        return definition.body.height, definition.body.width
    return definition.body.width, definition.body.height


def component_body_box(definition: ComponentDefinition, placement: Placement) -> Box:
    width, height = component_size(definition, placement)
    return (placement.x, placement.y, placement.x + width, placement.y + height)


def local_pin_anchor(definition: ComponentDefinition, pin: PinDefinition, placement: Placement | None = None) -> tuple[int, int]:
    if is_vertical(definition, placement):
        width, height = component_size(definition, placement)
        pin_index = definition.pins.index(pin)
        return (width // 2, 0) if pin_index == 0 else (width // 2, height)
    return local_pin_anchor_by_side(definition, pin.side, pin.position)


def local_pin_anchor_by_side(definition: ComponentDefinition, side: str, position: int) -> tuple[int, int]:
    offset = position * definition.body.pin_pitch
    if side == "left":
        return (0, offset)
    if side == "right":
        return (definition.body.width, offset)
    if side == "top":
        return (offset, 0)
    return (offset, definition.body.height)


def absolute_pin_point(definition: ComponentDefinition, placement: Placement, pin: PinDefinition) -> tuple[int, int]:
    x, y = local_pin_anchor(definition, pin, placement)
    return (placement.x + x, placement.y + y)


def visual_pin_side(definition: ComponentDefinition, pin: PinDefinition, placement: Placement | None = None) -> str:
    if is_vertical(definition, placement):
        return "top" if definition.pins.index(pin) == 0 else "bottom"
    return pin.side


def symbol_box(definition: ComponentDefinition, placement: Placement) -> Box:
    width, height = component_size(definition, placement)
    x, y = placement.x, placement.y
    renderer = definition.body.renderer
    if is_vertical(definition, placement):
        if renderer == "resistor":
            return (x + 16, y + 18, x + width - 16, y + height - 18)
        if renderer in {"capacitor", "polarized_capacitor"}:
            return (x + 18, y, x + width - 18, y + height)
        return (x, y, x + width, y + height)
    if renderer == "resistor":
        return (x, y + 22, x + width, y + height - 22)
    if renderer in {"capacitor", "polarized_capacitor"}:
        return (x, y + 18, x + width, y + height - 18)
    if renderer in {"led", "nmos", "pmos", "ground", "test_point"}:
        return (x, y, x + width, y + height)
    return component_body_box(definition, placement)


def inflate_box(box: Box, padding: float) -> Box:
    x1, y1, x2, y2 = box
    return (x1 - padding, y1 - padding, x2 + padding, y2 + padding)


def boxes_overlap(a: Box, b: Box) -> bool:
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


def segment_crosses_box(segment: Segment, box: Box) -> bool:
    x1, y1, x2, y2 = segment
    bx1, by1, bx2, by2 = box
    if y1 == y2 and by1 < y1 < by2:
        return max(min(x1, x2), bx1) < min(max(x1, x2), bx2)
    if x1 == x2 and bx1 < x1 < bx2:
        return max(min(y1, y2), by1) < min(max(y1, y2), by2)
    return False


def segments_collinear_overlap(a: Segment, b: Segment) -> bool:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    if ay1 == ay2 == by1 == by2:
        return max(min(ax1, ax2), min(bx1, bx2)) < min(max(ax1, ax2), max(bx1, bx2))
    if ax1 == ax2 == bx1 == bx2:
        return max(min(ay1, ay2), min(by1, by2)) < min(max(ay1, ay2), max(by1, by2))
    return False


def segments_intersect(a: Segment, b: Segment) -> bool:
    a1 = (a[0], a[1])
    a2 = (a[2], a[3])
    b1 = (b[0], b[1])
    b2 = (b[2], b[3])
    o1 = _orientation(a1, a2, b1)
    o2 = _orientation(a1, a2, b2)
    o3 = _orientation(b1, b2, a1)
    o4 = _orientation(b1, b2, a2)
    if o1 != o2 and o3 != o4:
        return True
    return (
        (o1 == 0 and _point_on_segment(b1, a1, a2))
        or (o2 == 0 and _point_on_segment(b2, a1, a2))
        or (o3 == 0 and _point_on_segment(a1, b1, b2))
        or (o4 == 0 and _point_on_segment(a2, b1, b2))
    )


def segments_intersect_away_from_shared_endpoint(a: Segment, b: Segment) -> bool:
    if segments_collinear_overlap(a, b):
        return True
    if not segments_intersect(a, b):
        return False
    shared_endpoints = {(a[0], a[1]), (a[2], a[3])} & {(b[0], b[1]), (b[2], b[3])}
    return not shared_endpoints


def _orientation(a: tuple[int, int], b: tuple[int, int], c: tuple[int, int]) -> int:
    value = (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[1] - b[1])
    if value == 0:
        return 0
    return 1 if value > 0 else 2


def _point_on_segment(point: tuple[int, int], start: tuple[int, int], end: tuple[int, int]) -> bool:
    return min(start[0], end[0]) <= point[0] <= max(start[0], end[0]) and min(start[1], end[1]) <= point[1] <= max(start[1], end[1])
