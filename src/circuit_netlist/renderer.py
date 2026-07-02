from __future__ import annotations

from html import escape
from typing import Callable

from .component_library import ComponentLibrary
from .geometry import (
    absolute_pin_point,
    component_body_box,
    component_size,
    local_pin_anchor,
    local_pin_anchor_by_side,
    segments_collinear_overlap,
    symbol_box,
)
from .models import Circuit, ComponentDefinition, Diagnostic, Layout, Placement, RoutedNet


RendererFn = Callable[[str, ComponentDefinition, Placement], str]
Box = tuple[float, float, float, float]
Segment = tuple[int, int, int, int]

MIN_LABEL_GAP = 8
MIN_WIRE_TO_TEXT_GAP = 6
MIN_SYMBOL_TO_TEXT_GAP = 8
MCU_CLEARANCE = 60
PIN_ESCAPE_DISTANCE = 28
POWER_PIN_ESCAPE_DISTANCE = 56
POWER_PIN_EDGE_LEAD = 12
POWER_SYMBOL_VERTICAL_OFFSET = 36
NET_LABEL_ESCAPE_DISTANCE = 24
MOSFET_SYMBOL_STYLE = "compact_no_bulk"


class RendererRegistry:
    """SVG symbol renderer registry keyed by component body renderer name."""

    def __init__(self) -> None:
        self._renderers: dict[str, RendererFn] = {}

    def register(self, name: str, renderer: RendererFn) -> None:
        self._renderers[name] = renderer

    def render_component(self, ref: str, definition: ComponentDefinition, placement: Placement) -> str:
        renderer = self._renderers.get(definition.body.renderer, render_block)
        return renderer(ref, definition, placement)


def render_circuit(circuit: Circuit, library: ComponentLibrary, layout: Layout, routes: list[RoutedNet], diagnostics: list[Diagnostic]) -> str:
    from .scene_builder import build_schematic_scene
    from .scene_renderer import render_scene_svg

    scene = build_schematic_scene(circuit, library, layout, routes)
    return render_scene_svg(scene, diagnostics)


class LabelPlacementContext:
    def __init__(self, circuit: Circuit, library: ComponentLibrary, layout: Layout, routes: list[RoutedNet]) -> None:
        self.body_boxes: list[Box] = []
        self.physical_boxes: list[Box] = []
        self.text_boxes: list[Box] = []
        self.stub_segments: list[Segment] = []
        self.wire_segments: list[Segment] = [segment for route in routes for segment in route.segments]
        for instance in circuit.components:
            definition = library.get(instance.component_id)
            placement = layout.components.get(instance.ref)
            if not definition or not placement:
                continue
            body_box = component_body_box(definition, placement)
            physical_symbol_box = symbol_box(definition, placement)
            self.body_boxes.append(body_box)
            self.body_boxes.append(physical_symbol_box)
            self.physical_boxes.append(body_box)
            self.physical_boxes.append(physical_symbol_box)
            if definition.category == "MCU":
                _, _, body_x2, body_y2 = body_box
                self.body_boxes.append(
                    (
                        placement.x - MCU_CLEARANCE,
                        placement.y - MCU_CLEARANCE + 20,
                        body_x2 + MCU_CLEARANCE,
                        body_y2 + MCU_CLEARANCE,
                    )
                )
            self._add_component_text_boxes(instance.ref, definition, placement)

    def _add_component_text_boxes(self, ref: str, definition: ComponentDefinition, placement: Placement) -> None:
        value = definition.name if not definition.part_number else definition.part_number
        ref_x, ref_y, ref_anchor, value_x, value_y, value_anchor = component_label_positions(definition, placement)
        self.reserve_text(ref, placement.x + ref_x, placement.y + ref_y, ref_anchor)
        self.reserve_text(value, placement.x + value_x, placement.y + value_y, value_anchor)
        for pin in definition.pins:
            lx, number_y, anchor = pin_label_position(definition, placement, pin, "number")
            _, name_y, _ = pin_label_position(definition, placement, pin, "name")
            lx += placement.x
            number_y += placement.y
            name_y += placement.y
            self.reserve_text(pin.number, lx, number_y, anchor)
            self.reserve_text(pin.name, lx, name_y, anchor)

    def reserve_text(self, text: str, x: float, y: float, anchor: str = "start") -> Box:
        box = text_box(text, x, y, anchor)
        self.text_boxes.append(inflate_box(box, MIN_LABEL_GAP))
        return box

    def collides(self, box: Box) -> bool:
        padded = inflate_box(box, MIN_LABEL_GAP)
        if any(boxes_overlap(padded, body) for body in self.body_boxes):
            return True
        if any(boxes_overlap(padded, text) for text in self.text_boxes):
            return True
        return any(segment_crosses_box(segment, inflate_box(box, MIN_WIRE_TO_TEXT_GAP)) for segment in self.wire_segments)

    def collides_with_body_or_wire(self, box: Box) -> bool:
        padded = inflate_box(box, MIN_LABEL_GAP)
        if any(boxes_overlap(padded, body) for body in self.body_boxes):
            return True
        return any(segment_crosses_box(segment, inflate_box(box, MIN_WIRE_TO_TEXT_GAP)) for segment in self.wire_segments)

    def segment_collides_with_body(self, segment: Segment) -> bool:
        return any(segment_crosses_box(segment, body) for body in self.physical_boxes)

    def segment_collides_with_wire(self, segment: Segment) -> bool:
        return any(segments_collinear_overlap(segment, other) for other in [*self.wire_segments, *self.stub_segments])

    def segment_collides_with_text(self, segment: Segment) -> bool:
        return any(segment_crosses_box(segment, box) for box in self.text_boxes)

    def reserve_stub(self, segment: Segment) -> None:
        self.stub_segments.append(segment)


def render_route(route: RoutedNet, context: LabelPlacementContext | None = None) -> str:
    parts = [f'<g id="net-{safe_id(route.name)}" class="net" data-net="{escape(route.name)}" data-render-style="{route.render_style}">']
    for index, (x1, y1, x2, y2) in enumerate(route.segments):
        parts.append(f'<line id="wire-{safe_id(route.name)}-{index}" class="wire" data-net="{escape(route.name)}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"/>')
    if route.render_style == "net_label":
        for index, endpoint in enumerate(route.endpoints):
            parts.append(render_net_label(route.name, endpoint, index, context))
    if route.render_style == "power_symbol":
        for index, endpoint in enumerate(route.endpoints):
            parts.append(render_power_symbol(route.name, endpoint, index, context))
    for x, y in route.junctions:
        parts.append(f'<circle class="junction" data-net="{escape(route.name)}" cx="{x}" cy="{y}" r="4"/>')
    for x, y, label in route.labels[:1]:
        parts.append(f'<text class="net-label" data-net="{escape(route.name)}" x="{x}" y="{y}">{escape(label)}</text>')
    parts.append("</g>")
    return "".join(parts)


def render_net_label(net_name: str, endpoint: dict[str, object], index: int, context: LabelPlacementContext | None = None) -> str:
    x = int(endpoint["x"])
    y = int(endpoint["y"])
    side = str(endpoint.get("side", "right"))
    segments, stub_x, stub_y, anchor, text_x, text_y, render_side = choose_net_label_position(net_name, x, y, side, context)
    if context:
        context.reserve_text(net_name, text_x, text_y, anchor)
        for segment in segments:
            context.reserve_stub(segment)
    return (
        f'<g id="net-label-{safe_id(net_name)}-{index}" class="net-label-endpoint" data-net="{escape(net_name)}" data-render-style="net_label">'
        f'{render_stub_lines(net_name, segments, "label-stub")}'
        f'<path class="label-flag" d="{label_flag_path(stub_x, stub_y, render_side)}"/>'
        f'<text class="net-label" data-net="{escape(net_name)}" x="{text_x}" y="{text_y}" text-anchor="{anchor}">{escape(net_name)}</text>'
        "</g>"
    )


def render_power_symbol(net_name: str, endpoint: dict[str, object], index: int, context: LabelPlacementContext | None = None) -> str:
    x = int(endpoint["x"])
    y = int(endpoint["y"])
    side = str(endpoint.get("side", "right"))
    if net_name in {"GND", "AGND", "DGND", "PGND"}:
        stub_segments, symbol_x, symbol_y = choose_ground_symbol_attachment(x, y, side, context)
        if context:
            context.reserve_text(net_name, symbol_x, symbol_y + 42, "middle")
            for segment in stub_segments:
                context.reserve_stub(segment)
        return (
            f'<g id="power-symbol-{safe_id(net_name)}-{index}" class="power-symbol ground-symbol" data-net="{escape(net_name)}" data-render-style="power_symbol">'
            f'{render_stub_lines(net_name, stub_segments, "power-stub ground-stub")}'
            f'<line class="power-shape" x1="{symbol_x}" y1="{symbol_y - 12}" x2="{symbol_x}" y2="{symbol_y}"/>'
            f'<line class="power-shape" x1="{symbol_x - 18}" y1="{symbol_y}" x2="{symbol_x + 18}" y2="{symbol_y}"/>'
            f'<line class="power-shape" x1="{symbol_x - 12}" y1="{symbol_y + 10}" x2="{symbol_x + 12}" y2="{symbol_y + 10}"/>'
            f'<line class="power-shape" x1="{symbol_x - 6}" y1="{symbol_y + 20}" x2="{symbol_x + 6}" y2="{symbol_y + 20}"/>'
            f'<text class="net-label ground-label" data-net="{escape(net_name)}" x="{symbol_x}" y="{symbol_y + 42}" text-anchor="middle">{escape(net_name)}</text>'
            "</g>"
        )
    stub_segments, symbol_x, symbol_y = choose_power_symbol_attachment(net_name, x, y, side, context)
    if context:
        context.reserve_text(net_name, symbol_x, symbol_y - 24, "middle")
        for segment in stub_segments:
            context.reserve_stub(segment)
    return (
        f'<g id="power-symbol-{safe_id(net_name)}-{index}" class="power-symbol" data-net="{escape(net_name)}" data-render-style="power_symbol">'
        f'{render_stub_lines(net_name, stub_segments)}'
        f'<path class="power-shape power-flag" d="M {symbol_x - 14} {symbol_y + 12} L {symbol_x} {symbol_y - 8} L {symbol_x + 14} {symbol_y + 12} Z"/>'
        f'<text class="net-label power-label" data-net="{escape(net_name)}" x="{symbol_x}" y="{symbol_y - 24}" text-anchor="middle">{escape(net_name)}</text>'
        "</g>"
    )


def power_stub_segments(x: int, y: int, side: str, vertical_end_y: int) -> list[Segment]:
    symbol_y = vertical_end_y - 12 if vertical_end_y < y else vertical_end_y + 12
    return attachment_segments(x, y, side, x + escape_dx(side, POWER_PIN_ESCAPE_DISTANCE), y + escape_dy(side, POWER_PIN_ESCAPE_DISTANCE), symbol_y)


def render_stub_lines(net_name: str, segments: list[Segment], css_class: str = "power-stub") -> str:
    return "".join(
        f'<line class="wire {css_class}" data-net="{escape(net_name)}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"/>'
        for x1, y1, x2, y2 in segments
    )


def choose_power_symbol_attachment(net_name: str, x: int, y: int, side: str, context: LabelPlacementContext | None) -> tuple[list[Segment], int, int]:
    return choose_power_attachment(net_name, x, y, side, context, "power")


def choose_ground_symbol_attachment(x: int, y: int, side: str, context: LabelPlacementContext | None) -> tuple[list[Segment], int, int]:
    return choose_power_attachment("GND", x, y, side, context, "ground")


def choose_power_attachment(net_name: str, x: int, y: int, side: str, context: LabelPlacementContext | None, kind: str) -> tuple[list[Segment], int, int]:
    best: tuple[int, list[Segment], int, int] | None = None
    escape_distances = [POWER_PIN_ESCAPE_DISTANCE, 72, 88, 104, 128, 152]
    vertical_offsets = [POWER_SYMBOL_VERTICAL_OFFSET, 56, 76, 96, 120, 150, 190, 240]
    if side in {"top", "bottom"}:
        for escape_distance in escape_distances:
            escape_x = x
            escape_y = y + escape_dy(side, escape_distance)
            symbol_x = x
            for vertical_offset in vertical_offsets:
                symbol_y = escape_y + vertical_offset if kind == "ground" else escape_y - vertical_offset
                attach_y = symbol_y - 12 if symbol_y > y else symbol_y + 12
                segments = [(x, y, symbol_x, attach_y)]
                collision_count = attachment_collision_count(segments, symbol_x, symbol_y, net_name, kind, context)
                length = abs(attach_y - y)
                escape_penalty = max(0, POWER_PIN_ESCAPE_DISTANCE - escape_distance)
                cost = collision_count * 1_000_000 + length * 4 + escape_penalty * 100
                if best is None or cost < best[0]:
                    best = (cost, segments, symbol_x, symbol_y)
                if collision_count == 0:
                    return segments, symbol_x, symbol_y
        assert best is not None
        return best[1], best[2], best[3]
    lateral_offsets = [0, 24, -24, 48, -48, 72, -72]
    for escape_distance in escape_distances:
        escape_x = x + escape_dx(side, escape_distance)
        escape_y = y + escape_dy(side, escape_distance)
        for lateral in lateral_offsets:
            symbol_x = escape_x + lateral
            for vertical_offset in vertical_offsets:
                symbol_y = escape_y + vertical_offset if kind == "ground" else escape_y - vertical_offset
                segments = attachment_segments(x, y, side, escape_x, escape_y, symbol_y, symbol_x)
                collision_count = attachment_collision_count(segments, symbol_x, symbol_y, net_name, kind, context)
                length = sum(abs(x1 - x2) + abs(y1 - y2) for x1, y1, x2, y2 in segments)
                bends = max(0, len(segments) - 1)
                lateral_penalty = abs(lateral)
                escape_penalty = max(0, POWER_PIN_ESCAPE_DISTANCE - escape_distance)
                cost = collision_count * 1_000_000 + length * 4 + bends * 30 + lateral_penalty * 2 + escape_penalty * 100
                if best is None or cost < best[0]:
                    best = (cost, segments, symbol_x, symbol_y)
                if collision_count == 0:
                    return segments, symbol_x, symbol_y
    assert best is not None
    return best[1], best[2], best[3]


def attachment_segments(
    x: int,
    y: int,
    side: str,
    escape_x: int,
    escape_y: int,
    symbol_y: int,
    symbol_x: int | None = None,
) -> list[Segment]:
    symbol_x = escape_x if symbol_x is None else symbol_x
    attach_y = symbol_y - 12 if symbol_y > escape_y else symbol_y + 12
    points = [(x, y), (escape_x, escape_y)]
    if symbol_x != escape_x:
        points.append((symbol_x, escape_y))
    points.append((symbol_x, attach_y))
    return segments_from_points(points)


def segments_from_points(points: list[tuple[int, int]]) -> list[Segment]:
    segments: list[Segment] = []
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        if x1 == x2 and y1 == y2:
            continue
        segments.append((x1, y1, x2, y2))
    return segments


def escape_dx(side: str, distance: int) -> int:
    if side == "left":
        return -distance
    if side == "right":
        return distance
    return 0


def escape_dy(side: str, distance: int) -> int:
    if side == "top":
        return -distance
    if side == "bottom":
        return distance
    return 0


def attachment_collision_count(segments: list[Segment], symbol_x: int, symbol_y: int, net_name: str, kind: str, context: LabelPlacementContext | None) -> int:
    if context is None:
        return 0
    count = 0
    for segment in segments:
        count += int(context.segment_collides_with_body(segment))
        count += int(context.segment_collides_with_text(segment))
        count += int(context.segment_collides_with_wire(segment))
    symbol_box = power_symbol_box(symbol_x, symbol_y, net_name, kind)
    padded_symbol = inflate_box(symbol_box, MIN_SYMBOL_TO_TEXT_GAP)
    count += sum(1 for body in context.body_boxes if boxes_overlap(padded_symbol, body))
    count += sum(1 for text in context.text_boxes if boxes_overlap(padded_symbol, text))
    count += sum(1 for segment in context.wire_segments if segment_crosses_box(segment, inflate_box(symbol_box, MIN_WIRE_TO_TEXT_GAP)))
    return count


def power_symbol_box(symbol_x: int, symbol_y: int, net_name: str, kind: str) -> Box:
    if kind == "ground":
        label = text_box(net_name, symbol_x, symbol_y + 42, "middle")
        shape = (symbol_x - 20, symbol_y - 12, symbol_x + 20, symbol_y + 24)
        return (
            min(label[0], shape[0]),
            min(label[1], shape[1]),
            max(label[2], shape[2]),
            max(label[3], shape[3]),
        )
    label = text_box(net_name, symbol_x, symbol_y - 24, "middle")
    shape = (symbol_x - 16, symbol_y - 10, symbol_x + 16, symbol_y + 14)
    return (
        min(label[0], shape[0]),
        min(label[1], shape[1]),
        max(label[2], shape[2]),
        max(label[3], shape[3]),
    )


def choose_net_label_position(
    net_name: str,
    x: int,
    y: int,
    preferred_side: str,
    context: LabelPlacementContext | None,
) -> tuple[list[Segment], int, int, str, int, int, str]:
    escape_distances = [NET_LABEL_ESCAPE_DISTANCE, 36, 52, 68, 92, 116]
    sides = unique_sides([preferred_side, "right", "left", "top", "bottom"])
    best: tuple[int, list[Segment], int, int, str, int, int, str] | None = None
    for escape_distance in escape_distances:
        escape_x = x + escape_dx(preferred_side, escape_distance)
        escape_y = y + escape_dy(preferred_side, escape_distance)
        for render_side in sides:
            for distance in range(1, 8):
                dx, dy, anchor, text_dx, text_dy = label_vector(render_side, distance)
                stub_x = escape_x + dx
                stub_y = escape_y + dy
                text_x = stub_x + text_dx
                text_y = stub_y + text_dy
                segments = segments_from_points([(x, y), (escape_x, escape_y), (stub_x, stub_y)])
                box = text_box(net_name, text_x, text_y, anchor)
                collision_count = label_collision_count(segments, box, context)
                length = sum(abs(x1 - x2) + abs(y1 - y2) for x1, y1, x2, y2 in segments)
                bends = max(0, len(segments) - 1)
                preferred_penalty = 0 if render_side == preferred_side else 40
                cost = collision_count * 1_000_000 + length * 4 + bends * 30 + preferred_penalty
                candidate = (cost, segments, stub_x, stub_y, anchor, text_x, text_y, render_side)
                if best is None or cost < best[0]:
                    best = candidate
                if collision_count == 0:
                    return segments, stub_x, stub_y, anchor, text_x, text_y, render_side
    assert best is not None
    return best[1], best[2], best[3], best[4], best[5], best[6], best[7]


def label_collision_count(segments: list[Segment], box: Box, context: LabelPlacementContext | None) -> int:
    if context is None:
        return 0
    count = int(context.collides(box))
    for segment in segments:
        count += int(context.segment_collides_with_body(segment))
        count += int(context.segment_collides_with_wire(segment))
        count += int(context.segment_collides_with_text(segment))
    return count


def choose_power_symbol_y(net_name: str, x: int, y: int, context: LabelPlacementContext | None) -> int:
    _, _, symbol_y = choose_power_symbol_attachment(net_name, x, y, "right", context)
    return symbol_y


def choose_ground_symbol_y(x: int, y: int, context: LabelPlacementContext | None) -> int:
    _, _, symbol_y = choose_ground_symbol_attachment(x, y, "right", context)
    return symbol_y


def unique_sides(sides: list[str]) -> list[str]:
    ordered: list[str] = []
    for side in sides:
        if side not in ordered:
            ordered.append(side)
    return ordered


def label_vector(side: str, distance: int = 1) -> tuple[int, int, str, int, int]:
    horizontal = 62 + (distance - 1) * 32
    vertical = 54 + (distance - 1) * 26
    if side == "left":
        return (-horizontal, 0, "end", -10, 5)
    if side == "top":
        return (0, -vertical, "middle", 0, -10)
    if side == "bottom":
        return (0, vertical, "middle", 0, 20)
    return (horizontal, 0, "start", 10, 5)


def label_flag_path(x: int, y: int, side: str) -> str:
    if side == "left":
        return f"M {x} {y} l -8 -6 h -54 v 12 h 54 z"
    if side == "top":
        return f"M {x} {y} l -7 -8 v -24 h 14 v 24 z"
    if side == "bottom":
        return f"M {x} {y} l -7 8 v 24 h 14 v -24 z"
    return f"M {x} {y} l 8 -6 h 54 v 12 h -54 z"


def text_box(text: str, x: float, y: float, anchor: str = "start") -> Box:
    width = max(10.0, len(text) * 7.4)
    height = 15.0
    if anchor == "end":
        return (x - width, y - height + 3, x, y + 4)
    if anchor == "middle":
        return (x - width / 2, y - height + 3, x + width / 2, y + 4)
    return (x, y - height + 3, x + width, y + 4)


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


def default_registry() -> RendererRegistry:
    registry = RendererRegistry()
    for name in ["functional_block", "battery", "solar_panel"]:
        registry.register(name, render_block)
    registry.register("dc_source", render_dc_source)
    registry.register("dip_ic", render_dip_ic)
    registry.register("resistor", render_resistor)
    registry.register("capacitor", render_capacitor)
    registry.register("polarized_capacitor", render_capacitor)
    registry.register("inductor", render_inductor)
    registry.register("op_amp", render_op_amp)
    registry.register("timer_555", render_timer_555)
    registry.register("led", render_led)
    registry.register("nmos", render_mosfet)
    registry.register("pmos", render_mosfet)
    registry.register("ground", render_ground)
    registry.register("test_point", render_test_point)
    return registry


def render_block(ref: str, definition: ComponentDefinition, placement: Placement) -> str:
    body = base_group(ref, definition, placement)
    width, height = component_size(definition, placement)
    body.append(f'<rect class="body" x="0" y="0" width="{width}" height="{height}" rx="4"/>')
    body.extend(pin_elements(ref, definition, placement))
    body.append(label_elements(ref, definition, placement))
    return close_group(body)


def render_dc_source(ref: str, definition: ComponentDefinition, placement: Placement) -> str:
    body = base_group(ref, definition, placement)
    body.append('<line class="symbol" x1="50" y1="0" x2="50" y2="24"/><line class="symbol" x1="50" y1="96" x2="50" y2="120"/>')
    body.append('<circle class="symbol" cx="50" cy="60" r="36"/>')
    body.append('<line class="symbol" x1="38" y1="42" x2="62" y2="42"/><line class="symbol" x1="50" y1="30" x2="50" y2="54"/>')
    body.append('<line class="symbol" x1="38" y1="82" x2="62" y2="82"/>')
    body.append('<circle class="pin-contact" cx="50" cy="0" r="5"/><circle class="pin-contact" cx="50" cy="120" r="5"/>')
    body.extend(pin_elements(ref, definition, placement))
    body.append(label_elements(ref, definition, placement))
    return close_group(body)


def render_dip_ic(ref: str, definition: ComponentDefinition, placement: Placement) -> str:
    body = base_group(ref, definition, placement)
    w, h = definition.body.width, definition.body.height
    body.append(f'<rect class="body ic-body" x="0" y="0" width="{w}" height="{h}" rx="6"/>')
    body.append(f'<path class="notch" d="M {w/2-18} 0 A 18 18 0 0 0 {w/2+18} 0"/>')
    body.append('<circle class="pin-one" cx="14" cy="24" r="4"/>')
    body.extend(pin_elements(ref, definition, placement))
    body.append(label_elements(ref, definition, placement))
    return close_group(body)


def render_resistor(ref: str, definition: ComponentDefinition, placement: Placement) -> str:
    body = base_group(ref, definition, placement)
    if placement.rotation % 180 == 90:
        body.append('<polyline class="symbol" points="40,0 40,25 25,32 55,46 25,60 55,74 25,88 55,102 40,110 40,140"/>')
    else:
        body.append('<polyline class="symbol" points="0,40 25,40 32,25 46,55 60,25 74,55 88,25 102,55 110,40 140,40"/>')
    body.extend(pin_elements(ref, definition, placement))
    body.append(label_elements(ref, definition, placement))
    return close_group(body)


def render_capacitor(ref: str, definition: ComponentDefinition, placement: Placement) -> str:
    body = base_group(ref, definition, placement)
    if placement.rotation % 180 == 90:
        body.append('<line class="symbol" x1="40" y1="0" x2="40" y2="55"/><line class="symbol" x1="18" y1="65" x2="62" y2="65"/><line class="symbol" x1="18" y1="85" x2="62" y2="85"/><line class="symbol" x1="40" y1="85" x2="40" y2="140"/>')
    else:
        body.append('<line class="symbol" x1="0" y1="40" x2="55" y2="40"/><line class="symbol" x1="65" y1="18" x2="65" y2="62"/><line class="symbol" x1="85" y1="18" x2="85" y2="62"/><line class="symbol" x1="85" y1="40" x2="140" y2="40"/>')
    if definition.body.renderer == "polarized_capacitor":
        body.append('<text class="polarity" x="48" y="20">+</text>')
    body.extend(pin_elements(ref, definition, placement))
    body.append(label_elements(ref, definition, placement))
    return close_group(body)


def render_inductor(ref: str, definition: ComponentDefinition, placement: Placement) -> str:
    body = base_group(ref, definition, placement)
    body.append('<path class="symbol" d="M0 40 H25 C25 20 45 20 45 40 C45 20 65 20 65 40 C65 20 85 20 85 40 C85 20 105 20 105 40 H140"/>')
    body.extend(pin_elements(ref, definition, placement))
    body.append(label_elements(ref, definition, placement))
    return close_group(body)


def render_op_amp(ref: str, definition: ComponentDefinition, placement: Placement) -> str:
    body = base_group(ref, definition, placement)
    body.append('<polygon class="symbol" points="20,20 20,100 140,60"/>')
    body.append('<line class="symbol" x1="0" y1="40" x2="20" y2="40"/><line class="symbol" x1="0" y1="80" x2="20" y2="80"/><line class="symbol" x1="140" y1="60" x2="160" y2="60"/>')
    body.append('<line class="symbol" x1="80" y1="0" x2="80" y2="40"/><line class="symbol" x1="80" y1="80" x2="80" y2="120"/>')
    body.append('<text class="polarity" x="28" y="44">-</text><text class="polarity" x="28" y="84">+</text>')
    body.append('<circle class="pin-contact" cx="0" cy="40" r="5"/><circle class="pin-contact" cx="0" cy="80" r="5"/><circle class="pin-contact" cx="160" cy="60" r="5"/><circle class="pin-contact" cx="80" cy="0" r="5"/><circle class="pin-contact" cx="80" cy="120" r="5"/>')
    body.extend(pin_elements(ref, definition, placement))
    body.append(label_elements(ref, definition, placement))
    return close_group(body)


def render_timer_555(ref: str, definition: ComponentDefinition, placement: Placement) -> str:
    body = base_group(ref, definition, placement)
    w, h = definition.body.width, definition.body.height
    body.append(f'<rect class="body ic-body timer-555-body" x="0" y="0" width="{w}" height="{h}" rx="6"/>')
    body.append(f'<path class="notch" d="M {w / 2 - 18} 0 A 18 18 0 0 0 {w / 2 + 18} 0"/>')
    body.append('<circle class="pin-one" cx="144" cy="206" r="4"/>')
    body.append(f'<text class="timer-title" x="{w / 2}" y="{h / 2 - 8}" text-anchor="middle">555</text>')
    body.append(f'<text class="timer-subtitle" x="{w / 2}" y="{h / 2 + 18}" text-anchor="middle">Astable</text>')
    body.extend(pin_elements(ref, definition, placement))
    body.append(label_elements(ref, definition, placement))
    return close_group(body)


def render_led(ref: str, definition: ComponentDefinition, placement: Placement) -> str:
    body = base_group(ref, definition, placement)
    body.append('<line class="symbol" x1="0" y1="40" x2="50" y2="40"/><polygon class="symbol-fill" points="50,15 50,65 90,40"/><line class="symbol" x1="92" y1="15" x2="92" y2="65"/><line class="symbol" x1="92" y1="40" x2="140" y2="40"/><path class="symbol" d="M105 10 l18 -18 M114 10 l18 -18"/>')
    body.append('<circle class="pin-contact" cx="0" cy="40" r="5"/><circle class="pin-contact" cx="140" cy="40" r="5"/>')
    body.extend(pin_elements(ref, definition, placement))
    body.append(label_elements(ref, definition, placement))
    return close_group(body)


def render_mosfet(ref: str, definition: ComponentDefinition, placement: Placement) -> str:
    body = base_group(ref, definition, placement)
    mode = str(definition.metadata.get("mosfet_mode", "enhancement"))
    is_pmos = definition.body.renderer == "pmos"
    body.append(f'<g class="mosfet-symbol" data-symbol-style="{MOSFET_SYMBOL_STYLE}" data-mosfet-mode="{escape(mode)}">')
    body.append('<line class="symbol mosfet-gate-lead" x1="0" y1="40" x2="42" y2="40"/>')
    body.append('<line class="symbol mosfet-gate" x1="52" y1="28" x2="52" y2="92"/>')
    body.append('<line class="symbol mosfet-top-lead" x1="80" y1="0" x2="80" y2="28"/>')
    body.append('<line class="symbol mosfet-bottom-lead" x1="80" y1="92" x2="80" y2="120"/>')
    if mode == "depletion":
        body.append('<line class="symbol mosfet-channel depletion-channel" x1="80" y1="28" x2="80" y2="92"/>')
    else:
        body.append('<line class="symbol mosfet-channel enhancement-channel" x1="80" y1="28" x2="80" y2="44"/><line class="symbol mosfet-channel enhancement-channel" x1="80" y1="54" x2="80" y2="66"/><line class="symbol mosfet-channel enhancement-channel" x1="80" y1="76" x2="80" y2="92"/>')
    body.append('<line class="symbol mosfet-top-terminal" x1="66" y1="32" x2="94" y2="32"/><line class="symbol mosfet-bottom-terminal" x1="66" y1="88" x2="94" y2="88"/>')
    arrow = '<path class="symbol mosfet-arrow pmos-arrow" d="M 78 36 L 66 36 M 71 31 L 66 36 L 71 41"/>' if is_pmos else '<path class="symbol mosfet-arrow nmos-arrow" d="M 64 84 L 76 84 M 71 79 L 76 84 L 71 89"/>'
    body.append(arrow)
    if definition.metadata.get("show_body_diode") is True:
        if is_pmos:
            body.append('<path class="symbol mosfet-body-diode pmos-body-diode" d="M 110 92 L 110 28"/><polygon class="symbol-fill mosfet-diode-triangle" points="110,44 102,60 118,60"/><line class="symbol mosfet-diode-bar" x1="102" y1="44" x2="118" y2="44"/>')
        else:
            body.append('<path class="symbol mosfet-body-diode nmos-body-diode" d="M 110 28 L 110 92"/><polygon class="symbol-fill mosfet-diode-triangle" points="110,76 102,60 118,60"/><line class="symbol mosfet-diode-bar" x1="102" y1="76" x2="118" y2="76"/>')
    if any(pin.name == "B" for pin in definition.pins):
        body.append('<line class="symbol mosfet-bulk-lead" x1="140" y1="60" x2="96" y2="60"/><circle class="pin-contact" cx="140" cy="60" r="5"/>')
    body.append("</g>")
    body.append('<circle class="pin-contact" cx="0" cy="40" r="5"/><circle class="pin-contact" cx="80" cy="0" r="5"/><circle class="pin-contact" cx="80" cy="120" r="5"/>')
    body.extend(pin_elements(ref, definition, placement))
    body.append(label_elements(ref, definition, placement))
    return close_group(body)


def render_ground(ref: str, definition: ComponentDefinition, placement: Placement) -> str:
    body = base_group(ref, definition, placement)
    body.append('<line class="symbol" x1="60" y1="0" x2="60" y2="30"/><line class="symbol" x1="25" y1="30" x2="95" y2="30"/><line class="symbol" x1="35" y1="45" x2="85" y2="45"/><line class="symbol" x1="45" y1="60" x2="75" y2="60"/>')
    body.extend(pin_elements(ref, definition, placement))
    body.append(label_elements(ref, definition, placement))
    return close_group(body)


def render_test_point(ref: str, definition: ComponentDefinition, placement: Placement) -> str:
    body = base_group(ref, definition, placement)
    body.append('<circle class="symbol" cx="50" cy="35" r="16"/><line class="symbol" x1="0" y1="35" x2="34" y2="35"/>')
    body.extend(pin_elements(ref, definition, placement))
    body.append(label_elements(ref, definition, placement))
    return close_group(body)


def base_group(ref: str, definition: ComponentDefinition, placement: Placement) -> list[str]:
    width, height = component_size(definition, placement)
    return [
        f'<g id="component-{safe_id(ref)}" class="component" data-ref="{escape(ref)}" data-component-id="{escape(definition.id)}" data-category="{escape(definition.category)}" data-orientation="{"vertical" if placement.rotation % 180 == 90 else "horizontal"}" transform="translate({placement.x},{placement.y})">',
        f'<rect class="hit-area" x="-24" y="-28" width="{width + 48}" height="{height + 56}"/>',
    ]


def close_group(parts: list[str]) -> str:
    parts.append("</g>")
    return "".join(parts)


def label_elements(ref: str, definition: ComponentDefinition, placement: Placement) -> str:
    value = definition.name if not definition.part_number else definition.part_number
    ref_x, ref_y, ref_anchor, value_x, value_y, value_anchor = component_label_positions(definition, placement)
    return (
        f'<text class="ref-label" x="{ref_x}" y="{ref_y}" text-anchor="{ref_anchor}">{escape(ref)}</text>'
        f'<text class="value-label" x="{value_x}" y="{value_y}" text-anchor="{value_anchor}">{escape(value)}</text>'
    )


def component_label_positions(definition: ComponentDefinition, placement: Placement) -> tuple[int, int, str, int, int, str]:
    width, height = component_size(definition, placement)
    if definition.body.renderer in {"nmos", "pmos"}:
        return -12, 14, "end", -12, height + 18, "end"
    anchors = [local_pin_anchor(definition, pin, placement) for pin in definition.pins]
    uses_top_bottom_pins = bool(anchors) and all(y in {0, height} for _, y in anchors)
    if uses_top_bottom_pins:
        return width + 12, 70, "start", width + 12, 88, "start"
    return 0, -10, "start", 0, height + 18, "start"


def pin_elements(ref: str, definition: ComponentDefinition, placement: Placement) -> list[str]:
    items: list[str] = []
    for pin in definition.pins:
        x, y = local_pin_anchor(definition, pin, placement)
        lx, number_y, anchor = pin_label_position(definition, placement, pin, "number")
        _, name_y, _ = pin_label_position(definition, placement, pin, "name")
        items.append(
            f'<g id="pin-{safe_id(ref)}-{safe_id(pin.number)}" class="pin" data-ref="{escape(ref)}" data-pin-number="{escape(pin.number)}" data-pin-name="{escape(pin.name)}" data-electrical-type="{pin.electrical_type.value}">'
            f'<circle class="pin-dot" cx="{x}" cy="{y}" r="4"/>'
            f'<text class="pin-number" x="{lx}" y="{number_y}" text-anchor="{anchor}">{escape(pin.number)}</text>'
            f'<text class="pin-name" x="{lx}" y="{name_y}" text-anchor="{anchor}">{escape(pin.name)}</text>'
            "</g>"
        )
    return items


def pin_label_position(definition: ComponentDefinition, placement: Placement, pin, label_kind: str) -> tuple[int, int, str]:
    x, y = local_pin_anchor(definition, pin, placement)
    width, height = component_size(definition, placement)
    if x == 0:
        side = "left"
    elif x == width:
        side = "right"
    elif y == 0:
        side = "top"
    elif y == height:
        side = "bottom"
    else:
        side = pin.side
    if side == "left":
        return x + 22, y - 5 if label_kind == "number" else y + 12, "start"
    if side == "right":
        return x - 22, y - 5 if label_kind == "number" else y + 12, "end"
    if side == "top":
        return x + 5, y + 30 if label_kind == "number" else y + 46, "start"
    return x + 5, y - 46 if label_kind == "number" else y - 30, "start"


def pin_anchor(definition: ComponentDefinition, side: str, position: int) -> tuple[int, int]:
    return local_pin_anchor_by_side(definition, side, position)


def safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value)
