from __future__ import annotations

from math import floor

from .component_library import ComponentLibrary
from .geometry import (
    absolute_pin_point,
    boxes_overlap,
    component_body_box,
    component_size,
    inflate_box,
    local_pin_anchor,
    segment_crosses_box,
    segments_collinear_overlap,
    symbol_box,
    visual_pin_side,
)
from .models import Circuit, ComponentDefinition, Layout, Placement, PinDefinition, RenderQualityMode, RoutedNet


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
GROUND_NETS = {"GND", "AGND", "DGND", "PGND"}


class SpatialIndex:
    def __init__(self, cell_size: int = 96) -> None:
        self.cell_size = cell_size
        self.items: list[tuple[Box, object]] = []
        self.buckets: dict[tuple[int, int], list[int]] = {}

    def add(self, box: Box, item: object) -> None:
        index = len(self.items)
        self.items.append((box, item))
        for cell in self._cells_for_box(box):
            self.buckets.setdefault(cell, []).append(index)

    def query(self, box: Box) -> list[object]:
        seen: set[int] = set()
        result: list[object] = []
        for cell in self._cells_for_box(box):
            for index in self.buckets.get(cell, []):
                if index in seen:
                    continue
                seen.add(index)
                item_box, item = self.items[index]
                if boxes_overlap(box, item_box):
                    result.append(item)
        return result

    def _cells_for_box(self, box: Box) -> list[tuple[int, int]]:
        min_x, min_y, max_x, max_y = box
        if max_x < min_x:
            min_x, max_x = max_x, min_x
        if max_y < min_y:
            min_y, max_y = max_y, min_y
        x1 = floor(min_x / self.cell_size)
        x2 = floor(max_x / self.cell_size)
        y1 = floor(min_y / self.cell_size)
        y2 = floor(max_y / self.cell_size)
        return [(x, y) for x in range(x1, x2 + 1) for y in range(y1, y2 + 1)]


class LabelPlacementContext:
    def __init__(
        self,
        circuit: Circuit,
        library: ComponentLibrary,
        layout: Layout,
        routes: list[RoutedNet],
        quality: RenderQualityMode | str = RenderQualityMode.STRICT,
    ) -> None:
        self.quality_mode = quality if isinstance(quality, RenderQualityMode) else RenderQualityMode(quality)
        self.metrics: dict[str, float] = {
            "label_candidate_count": 0,
            "power_candidate_count": 0,
            "collision_check_count": 0,
        }
        self.body_boxes: list[Box] = []
        self.physical_boxes: list[Box] = []
        self.symbol_clearance_boxes: list[Box] = []
        self.text_boxes: list[Box] = []
        self.attachment_boxes: list[Box] = []
        self.pin_escape_boxes: list[tuple[tuple[int, int, str], Box]] = []
        self.stub_segments: list[Segment] = []
        self.wire_segments: list[Segment] = []
        self.body_index = SpatialIndex()
        self.physical_index = SpatialIndex()
        self.symbol_clearance_index = SpatialIndex()
        self.text_index = SpatialIndex()
        self.attachment_index = SpatialIndex()
        self.pin_escape_index = SpatialIndex()
        self.wire_index = SpatialIndex()
        self.stub_index = SpatialIndex()
        for route in routes:
            for segment in route.segments:
                self._add_wire_segment(segment)
        for instance in circuit.components:
            definition = library.get(instance.component_id)
            placement = layout.components.get(instance.ref)
            if not definition or not placement:
                continue
            body_box = component_body_box(definition, placement)
            physical_symbol_box = symbol_box(definition, placement)
            self._add_body_box(body_box)
            self._add_body_box(physical_symbol_box)
            self._add_physical_box(body_box)
            self._add_physical_box(physical_symbol_box)
            self._add_symbol_clearance_box(inflate_box(physical_symbol_box, 10))
            if definition.category == "MCU":
                _, _, body_x2, body_y2 = body_box
                self._add_body_box(
                    (
                        placement.x - MCU_CLEARANCE,
                        placement.y - MCU_CLEARANCE + 20,
                        body_x2 + MCU_CLEARANCE,
                        body_y2 + MCU_CLEARANCE,
                    )
                )
            self._add_component_text_boxes(instance.ref, definition, placement)
            self._add_pin_escape_boxes(definition, placement)

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

    def _add_pin_escape_boxes(self, definition: ComponentDefinition, placement: Placement) -> None:
        for pin in definition.pins:
            x, y = absolute_pin_point(definition, placement, pin)
            side = visual_pin_side(definition, pin, placement)
            end_x = x + escape_dx(side, POWER_PIN_ESCAPE_DISTANCE)
            end_y = y + escape_dy(side, POWER_PIN_ESCAPE_DISTANCE)
            self._add_pin_escape_box((x, y, side), segment_box((x, y, end_x, end_y), 10))

    @property
    def interactive(self) -> bool:
        return self.quality_mode == RenderQualityMode.INTERACTIVE

    def _add_body_box(self, box: Box) -> None:
        self.body_boxes.append(box)
        self.body_index.add(box, box)

    def _add_physical_box(self, box: Box) -> None:
        self.physical_boxes.append(box)
        self.physical_index.add(box, box)

    def _add_symbol_clearance_box(self, box: Box) -> None:
        self.symbol_clearance_boxes.append(box)
        self.symbol_clearance_index.add(box, box)

    def _add_text_box(self, box: Box) -> None:
        self.text_boxes.append(box)
        self.text_index.add(box, box)

    def _add_attachment_box(self, box: Box) -> None:
        self.attachment_boxes.append(box)
        self.attachment_index.add(box, box)

    def _add_pin_escape_box(self, key: tuple[int, int, str], box: Box) -> None:
        item = (key, box)
        self.pin_escape_boxes.append(item)
        self.pin_escape_index.add(box, item)

    def _add_wire_segment(self, segment: Segment) -> None:
        self.wire_segments.append(segment)
        self.wire_index.add(segment_box(segment, 1), segment)

    def _add_stub_segment(self, segment: Segment) -> None:
        self.stub_segments.append(segment)
        self.stub_index.add(segment_box(segment, 1), segment)

    def _count_checks(self, items: list[object]) -> list[object]:
        self.metrics["collision_check_count"] += len(items)
        return items

    def body_boxes_near(self, box: Box) -> list[Box]:
        return [item for item in self._count_checks(self.body_index.query(box)) if isinstance(item, tuple)]

    def text_boxes_near(self, box: Box) -> list[Box]:
        return [item for item in self._count_checks(self.text_index.query(box)) if isinstance(item, tuple)]

    def attachment_boxes_near(self, box: Box) -> list[Box]:
        return [item for item in self._count_checks(self.attachment_index.query(box)) if isinstance(item, tuple)]

    def segments_near_box(self, box: Box) -> list[Segment]:
        wire_segments = self._count_checks(self.wire_index.query(box))
        stub_segments = self._count_checks(self.stub_index.query(box))
        return [item for item in [*wire_segments, *stub_segments] if isinstance(item, tuple)]

    def reserve_text(self, text: str, x: float, y: float, anchor: str = "start") -> Box:
        box = text_box(text, x, y, anchor)
        self._add_text_box(inflate_box(box, MIN_LABEL_GAP))
        return box

    def collides(self, box: Box) -> bool:
        padded = inflate_box(box, MIN_LABEL_GAP)
        if any(boxes_overlap(padded, body) for body in self.body_boxes_near(padded)):
            return True
        if any(boxes_overlap(padded, symbol) for symbol in self._count_checks(self.symbol_clearance_index.query(padded))):
            return True
        if any(boxes_overlap(padded, text) for text in self.text_boxes_near(padded)):
            return True
        if any(boxes_overlap(padded, attachment) for attachment in self.attachment_boxes_near(padded)):
            return True
        wire_box = inflate_box(box, MIN_WIRE_TO_TEXT_GAP)
        return any(segment_crosses_box(segment, wire_box) for segment in self.segments_near_box(wire_box))

    def segment_collides_with_body(self, segment: Segment) -> bool:
        candidates = self._count_checks(self.physical_index.query(segment_box(segment, 1)))
        return any(segment_crosses_box(segment, body) for body in candidates)

    def segment_collides_with_symbol_clearance(self, segment: Segment) -> bool:
        candidates = self._count_checks(self.symbol_clearance_index.query(segment_box(segment, 1)))
        return any(segment_crosses_box(segment, body) for body in candidates)

    def segment_collides_with_wire(self, segment: Segment) -> bool:
        return any(segments_collinear_overlap(segment, other) for other in self.segments_near_box(segment_box(segment, 1)))

    def segment_collides_with_text(self, segment: Segment) -> bool:
        candidates = self.text_boxes_near(segment_box(segment, 1))
        return any(segment_crosses_box(segment, box) for box in candidates)

    def segment_collides_with_attachment(self, segment: Segment) -> bool:
        candidates = self.attachment_boxes_near(segment_box(segment, 1))
        return any(segment_crosses_box(segment, box) for box in candidates)

    def reserve_stub(self, segment: Segment) -> None:
        self._add_stub_segment(segment)

    def reserve_label_shape(self, box: Box) -> None:
        self._add_attachment_box(inflate_box(box, MIN_LABEL_GAP))

    def reserve_symbol(self, box: Box) -> None:
        self._add_attachment_box(inflate_box(box, MIN_SYMBOL_TO_TEXT_GAP))

    def symbol_collides_with_other_pin_escape(self, box: Box, source_key: tuple[int, int, str]) -> bool:
        padded = inflate_box(box, MIN_SYMBOL_TO_TEXT_GAP)
        candidates = self._count_checks(self.pin_escape_index.query(padded))
        return any(key != source_key and boxes_overlap(padded, escape_box) for key, escape_box in candidates)


def component_label_positions(definition: ComponentDefinition, placement: Placement) -> tuple[int, int, str, int, int, str]:
    width, height = component_size(definition, placement)
    if definition.body.renderer in {"nmos", "pmos"}:
        return -48, -8, "end", -48, -30, "end"
    anchors = [local_pin_anchor(definition, pin, placement) for pin in definition.pins]
    uses_top_bottom_pins = bool(anchors) and all(y in {0, height} for _, y in anchors)
    if uses_top_bottom_pins:
        return width + 12, 70, "start", width + 12, 88, "start"
    return 0, -10, "start", 0, height + 18, "start"


def pin_label_position(definition: ComponentDefinition, placement: Placement, pin: PinDefinition, label_kind: str) -> tuple[int, int, str]:
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


def text_box(text: str, x: float, y: float, anchor: str = "start") -> Box:
    width = max(10.0, len(text) * 7.4)
    height = 15.0
    if anchor == "end":
        return (x - width, y - height + 3, x, y + 4)
    if anchor == "middle":
        return (x - width / 2, y - height + 3, x + width / 2, y + 4)
    return (x, y - height + 3, x + width, y + 4)


def choose_power_symbol_attachment(net_name: str, x: int, y: int, side: str, context: LabelPlacementContext | None) -> tuple[list[Segment], int, int]:
    return choose_power_attachment(net_name, x, y, side, context, "power")


def choose_ground_symbol_attachment(x: int, y: int, side: str, context: LabelPlacementContext | None) -> tuple[list[Segment], int, int]:
    return choose_power_attachment("GND", x, y, side, context, "ground")


def choose_power_attachment(net_name: str, x: int, y: int, side: str, context: LabelPlacementContext | None, kind: str) -> tuple[list[Segment], int, int]:
    best: tuple[int, list[Segment], int, int] | None = None
    if context and context.interactive:
        escape_distances = [POWER_PIN_ESCAPE_DISTANCE, 88, 120]
        vertical_offsets = [POWER_SYMBOL_VERTICAL_OFFSET, 56, 84, 120]
        lateral_offsets = [0, 32, -32, 64, -64, 96, -96]
    else:
        escape_distances = [POWER_PIN_ESCAPE_DISTANCE, 72, 88, 104, 128, 152]
        vertical_offsets = [POWER_SYMBOL_VERTICAL_OFFSET, 56, 76, 96, 120, 150, 190, 240]
        lateral_offsets = [0, 24, -24, 48, -48, 72, -72, 96, -96, 128, -128, 160, -160]
    if side in {"top", "bottom"}:
        for escape_distance in escape_distances:
            escape_y = y + escape_dy(side, escape_distance)
            for lateral in lateral_offsets:
                symbol_x = x + lateral
                for vertical_offset in vertical_offsets:
                    if context:
                        context.metrics["power_candidate_count"] += 1
                    symbol_y = escape_y + vertical_offset if kind == "ground" else escape_y - vertical_offset
                    segments = attachment_segments(x, y, side, x, escape_y, symbol_y, symbol_x)
                    collision_count = attachment_collision_count(segments, symbol_x, symbol_y, net_name, kind, context, (x, y, side))
                    length = sum(abs(x1 - x2) + abs(y1 - y2) for x1, y1, x2, y2 in segments)
                    bends = max(0, len(segments) - 1)
                    cost = collision_count * 1_000_000 + length * 4 + bends * 30 + abs(lateral) * 3
                    if best is None or cost < best[0]:
                        best = (cost, segments, symbol_x, symbol_y)
                    if collision_count == 0:
                        return segments, symbol_x, symbol_y
        assert best is not None
        return best[1], best[2], best[3]
    for escape_distance in escape_distances:
        escape_x = x + escape_dx(side, escape_distance)
        escape_y = y + escape_dy(side, escape_distance)
        for lateral in lateral_offsets:
            symbol_x = escape_x + lateral
            for vertical_offset in vertical_offsets:
                if context:
                    context.metrics["power_candidate_count"] += 1
                symbol_y = escape_y + vertical_offset if kind == "ground" else escape_y - vertical_offset
                segments = attachment_segments(x, y, side, escape_x, escape_y, symbol_y, symbol_x)
                collision_count = attachment_collision_count(segments, symbol_x, symbol_y, net_name, kind, context, (x, y, side))
                length = sum(abs(x1 - x2) + abs(y1 - y2) for x1, y1, x2, y2 in segments)
                bends = max(0, len(segments) - 1)
                cost = collision_count * 1_000_000 + length * 4 + bends * 30 + abs(lateral) * 2
                if best is None or cost < best[0]:
                    best = (cost, segments, symbol_x, symbol_y)
                if collision_count == 0:
                    return segments, symbol_x, symbol_y
    assert best is not None
    return best[1], best[2], best[3]


def attachment_segments(x: int, y: int, side: str, escape_x: int, escape_y: int, symbol_y: int, symbol_x: int | None = None) -> list[Segment]:
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


def segment_box(segment: Segment, padding: float = 0) -> Box:
    x1, y1, x2, y2 = segment
    return inflate_box((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)), padding)


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


def attachment_collision_count(
    segments: list[Segment],
    symbol_x: int,
    symbol_y: int,
    net_name: str,
    kind: str,
    context: LabelPlacementContext | None,
    source_key: tuple[int, int, str],
) -> int:
    if context is None:
        return 0
    count = 0
    for segment in segments:
        count += 100 * int(context.segment_collides_with_body(segment))
        count += 100 * int(context.segment_collides_with_symbol_clearance(segment))
        count += 25 * int(context.segment_collides_with_text(segment))
        count += 25 * int(context.segment_collides_with_attachment(segment))
        count += 25 * int(context.segment_collides_with_wire(segment))
    symbol_bounds = power_symbol_box(symbol_x, symbol_y, net_name, kind)
    padded_symbol = inflate_box(symbol_bounds, MIN_SYMBOL_TO_TEXT_GAP)
    count += 25 * sum(
        1
        for segment in segments
        if segment_crosses_box(segment, padded_symbol) and not segment_endpoint_touches_box(segment, padded_symbol)
    )
    count += 100 * sum(1 for body in context.body_boxes_near(padded_symbol) if boxes_overlap(padded_symbol, body))
    count += 25 * sum(1 for text in context.text_boxes_near(padded_symbol) if boxes_overlap(padded_symbol, text))
    count += 25 * sum(1 for attachment in context.attachment_boxes_near(padded_symbol) if boxes_overlap(padded_symbol, attachment))
    count += 5 * int(context.symbol_collides_with_other_pin_escape(symbol_bounds, source_key))
    wire_box = inflate_box(symbol_bounds, MIN_WIRE_TO_TEXT_GAP)
    count += 25 * sum(1 for segment in context.segments_near_box(wire_box) if segment_crosses_box(segment, wire_box))
    return count


def power_symbol_box(symbol_x: int, symbol_y: int, net_name: str, kind: str) -> Box:
    if kind == "ground":
        label = text_box(net_name, symbol_x, symbol_y + 42, "middle")
        shape = (symbol_x - 20, symbol_y - 12, symbol_x + 20, symbol_y + 24)
        return (min(label[0], shape[0]), min(label[1], shape[1]), max(label[2], shape[2]), max(label[3], shape[3]))
    label = text_box(net_name, symbol_x, symbol_y - 24, "middle")
    shape = (symbol_x - 16, symbol_y - 10, symbol_x + 16, symbol_y + 14)
    return (min(label[0], shape[0]), min(label[1], shape[1]), max(label[2], shape[2]), max(label[3], shape[3]))


def segment_endpoint_touches_box(segment: Segment, box: Box) -> bool:
    x1, y1, x2, y2 = segment
    return point_in_box((x1, y1), box) or point_in_box((x2, y2), box)


def point_in_box(point: tuple[int, int], box: Box) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def choose_net_label_position(
    net_name: str,
    x: int,
    y: int,
    preferred_side: str,
    context: LabelPlacementContext | None,
) -> tuple[list[Segment], int, int, str, int, int, str]:
    escape_distances = [NET_LABEL_ESCAPE_DISTANCE, 36, 56] if context and context.interactive else [NET_LABEL_ESCAPE_DISTANCE, 36, 52, 68, 92, 116]
    sides = unique_sides([preferred_side, "right", "left", "top", "bottom"])
    label_distances = range(1, 5) if context and context.interactive else range(1, 8)
    best: tuple[int, list[Segment], int, int, str, int, int, str] | None = None
    for escape_distance in escape_distances:
        escape_x = x + escape_dx(preferred_side, escape_distance)
        escape_y = y + escape_dy(preferred_side, escape_distance)
        for render_side in sides:
            for distance in label_distances:
                if context:
                    context.metrics["label_candidate_count"] += 1
                dx, dy, anchor, text_dx, text_dy = label_vector(render_side, distance)
                stub_x = escape_x + dx
                stub_y = escape_y + dy
                text_x = stub_x + text_dx
                text_y = stub_y + text_dy
                segments = segments_from_points([(x, y), (escape_x, escape_y), (stub_x, stub_y)])
                box = text_box(net_name, text_x, text_y, anchor)
                flag_box = label_flag_box(stub_x, stub_y, render_side)
                collision_count = label_collision_count(segments, box, flag_box, context)
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


def label_collision_count(segments: list[Segment], box: Box, flag_box: Box, context: LabelPlacementContext | None) -> int:
    if context is None:
        return 0
    count = int(context.collides(box)) + int(context.collides(flag_box))
    for segment in segments:
        count += 100 * int(context.segment_collides_with_body(segment))
        count += 100 * int(context.segment_collides_with_symbol_clearance(segment))
        count += 25 * int(context.segment_collides_with_wire(segment))
        count += 25 * int(context.segment_collides_with_text(segment))
        count += 25 * int(context.segment_collides_with_attachment(segment))
    return count


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


def label_flag_box(x: int, y: int, side: str) -> Box:
    if side == "left":
        return (x - 62, y - 6, x, y + 6)
    if side == "top":
        return (x - 7, y - 32, x + 7, y)
    if side == "bottom":
        return (x - 7, y, x + 7, y + 32)
    return (x, y - 6, x + 62, y + 6)


def safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value)
