from __future__ import annotations

from typing import Any

from .component_library import ComponentLibrary
from .geometry import absolute_pin_point, component_body_box, component_size, inflate_box, symbol_box
from .models import Circuit, ComponentDefinition, Layout, Placement, RoutedNet
from .scene import Bounds, LineSegment, Point, RenderPrimitive, SceneElement, SchematicScene, TextGeometry
from .schematic_geometry import (
    GROUND_NETS,
    LabelPlacementContext,
    choose_ground_symbol_attachment,
    choose_net_label_position,
    choose_power_symbol_attachment,
    component_label_positions,
    label_flag_path,
    pin_label_position,
    power_symbol_box,
    safe_id,
    text_box,
)
from .symbol_geometry import component_symbol_primitives


def build_schematic_scene(circuit: Circuit, library: ComponentLibrary, layout: Layout, routes: list[RoutedNet]) -> SchematicScene:
    """Build the canonical geometry scene shared by rendering, DRC, and hit testing."""
    context = LabelPlacementContext(circuit, library, layout, routes)
    elements: list[SceneElement] = []
    for route_index, route in enumerate(routes):
        elements.extend(_route_elements(route, route_index, context))
    for component_index, instance in enumerate(circuit.components):
        definition = library.get(instance.component_id)
        placement = layout.components.get(instance.ref)
        if definition and placement:
            elements.extend(_component_elements(instance.ref, definition, placement, component_index))
    canvas_bounds = _scene_canvas_bounds(layout, elements)
    return SchematicScene(
        circuit_name=circuit.name,
        canvas_bounds=canvas_bounds,
        elements=elements,
        metadata={
            "component_count": len(circuit.components),
            "net_count": len(circuit.nets),
            "route_count": len(routes),
            "orientations": {ref: placement.rotation for ref, placement in sorted(layout.components.items())},
            "canvas": dict(layout.canvas),
        },
    )


def _route_elements(route: RoutedNet, route_index: int, context: Any) -> list[SceneElement]:
    elements: list[SceneElement] = []
    route_id = f"net-{safe_id(route.name)}"
    for index, segment in enumerate(route.segments):
        svg_id = f"wire-{safe_id(route.name)}-{index}"
        elements.append(
            _segment_element(
                element_id=f"{route_id}:wire:{index}",
                kind="wire",
                route=route,
                segment=segment,
                source_ref="",
                parent_id=route_id,
                z_index=route_index * 100 + index,
                svg_id=svg_id,
            )
        )
    if route.render_style == "net_label":
        for index, endpoint in enumerate(route.endpoints):
            x = int(endpoint["x"])
            y = int(endpoint["y"])
            side = str(endpoint.get("side", "right"))
            segments, stub_x, stub_y, anchor, text_x, text_y, render_side = choose_net_label_position(route.name, x, y, side, context)
            for segment in segments:
                context.reserve_stub(segment)
            context.reserve_text(route.name, text_x, text_y, anchor)
            endpoint_id = f"net-label-{safe_id(route.name)}-{index}"
            flag_bounds = Bounds(min_x=min(stub_x, text_x) - 72, min_y=min(stub_y, text_y) - 24, max_x=max(stub_x, text_x) + 72, max_y=max(stub_y, text_y) + 24)
            elements.append(
                SceneElement(
                    id=endpoint_id,
                    kind="net_label_endpoint",
                    layer="wires",
                    owner_id=route_id,
                    parent_id=route_id,
                    net_name=route.name,
                    bounds=flag_bounds,
                    collision_bounds=flag_bounds,
                    hit_bounds=flag_bounds.expanded(4),
                    primitives=[RenderPrimitive(kind="path", style_class="label-flag", geometry={"d": label_flag_path(stub_x, stub_y, render_side)})],
                    selectable=True,
                    z_index=route_index * 100 + 18 + index,
                    metadata={"render_style": "net_label"},
                )
            )
            for segment_index, segment in enumerate(segments):
                elements.append(
                    _segment_element(
                        element_id=f"{endpoint_id}:stub:{segment_index}",
                        kind="wire_stub",
                        route=route,
                        segment=segment,
                        source_ref=str(endpoint.get("component_ref", "")),
                        parent_id=route_id,
                        z_index=route_index * 100 + 20 + segment_index,
                        wire_kind="label-stub",
                    )
                )
            label_box = text_box(route.name, text_x, text_y, anchor)
            elements.append(
                _text_element(
                    element_id=f"{endpoint_id}:text",
                    kind="net_label",
                    layer="wires",
                    text=route.name,
                    x=text_x,
                    y=text_y,
                    anchor=anchor,
                    bounds=label_box,
                    parent_id=route_id,
                    net_name=route.name,
                    z_index=route_index * 100 + 40 + index,
                    metadata={"text_role": "net_label", "attachment": True},
                )
            )
    if route.render_style == "power_symbol":
        for index, endpoint in enumerate(route.endpoints):
            x = int(endpoint["x"])
            y = int(endpoint["y"])
            side = str(endpoint.get("side", "right"))
            endpoint_id = f"power-symbol-{safe_id(route.name)}-{index}"
            is_ground = route.name in GROUND_NETS
            if is_ground:
                stub_segments, symbol_x, symbol_y = choose_ground_symbol_attachment(x, y, side, context)
                context.reserve_text(route.name, symbol_x, symbol_y + 42, "middle")
                label_y = symbol_y + 42
                label_anchor = "middle"
                symbol_kind = "ground_symbol"
                stub_class = "power-stub ground-stub"
                symbol_primitives = [
                    RenderPrimitive(kind="line", style_class="power-shape", geometry={"x1": symbol_x, "y1": symbol_y - 12, "x2": symbol_x, "y2": symbol_y}),
                    RenderPrimitive(kind="line", style_class="power-shape", geometry={"x1": symbol_x - 18, "y1": symbol_y, "x2": symbol_x + 18, "y2": symbol_y}),
                    RenderPrimitive(kind="line", style_class="power-shape", geometry={"x1": symbol_x - 12, "y1": symbol_y + 10, "x2": symbol_x + 12, "y2": symbol_y + 10}),
                    RenderPrimitive(kind="line", style_class="power-shape", geometry={"x1": symbol_x - 6, "y1": symbol_y + 20, "x2": symbol_x + 6, "y2": symbol_y + 20}),
                ]
            else:
                stub_segments, symbol_x, symbol_y = choose_power_symbol_attachment(route.name, x, y, side, context)
                context.reserve_text(route.name, symbol_x, symbol_y - 24, "middle")
                label_y = symbol_y - 24
                label_anchor = "middle"
                symbol_kind = "power_symbol"
                stub_class = "power-stub"
                symbol_primitives = [
                    RenderPrimitive(kind="path", style_class="power-shape power-flag", geometry={"d": f"M {symbol_x - 14} {symbol_y + 12} L {symbol_x} {symbol_y - 8} L {symbol_x + 14} {symbol_y + 12} Z"})
                ]
            for segment in stub_segments:
                context.reserve_stub(segment)
            for segment_index, segment in enumerate(stub_segments):
                elements.append(
                    _segment_element(
                        element_id=f"{endpoint_id}:stub:{segment_index}",
                        kind="wire_stub",
                        route=route,
                        segment=segment,
                        source_ref=str(endpoint.get("component_ref", "")),
                        parent_id=route_id,
                        z_index=route_index * 100 + 50 + segment_index,
                        wire_kind="power-stub",
                        metadata={"css_class": stub_class},
                    )
                )
            symbol_bounds = power_symbol_box(symbol_x, symbol_y, route.name, "ground" if is_ground else "power")
            elements.append(
                SceneElement(
                    id=f"{endpoint_id}:symbol",
                    kind=symbol_kind,
                    layer="wires",
                    owner_id=route_id,
                    parent_id=route_id,
                    net_name=route.name,
                    bounds=Bounds.from_tuple(symbol_bounds),
                    collision_bounds=Bounds.from_tuple(symbol_bounds),
                    hit_bounds=Bounds.from_tuple(symbol_bounds).expanded(8),
                    primitives=symbol_primitives,
                    z_index=route_index * 100 + 70 + index,
                    metadata={"attachment": True, "source_ref": str(endpoint.get("component_ref", ""))},
                    selectable=True,
                )
            )
            label_box = text_box(route.name, symbol_x, label_y, label_anchor)
            elements.append(
                _text_element(
                    element_id=f"{endpoint_id}:label",
                    kind="power_label" if not is_ground else "ground_label",
                    layer="wires",
                    text=route.name,
                    x=symbol_x,
                    y=label_y,
                    anchor=label_anchor,
                    bounds=label_box,
                    parent_id=route_id,
                    net_name=route.name,
                    z_index=route_index * 100 + 80 + index,
                    metadata={"text_role": "power_label", "attachment": True},
                )
            )
    for index, (x, y) in enumerate(route.junctions):
        bounds = Bounds(min_x=x - 4, min_y=y - 4, max_x=x + 4, max_y=y + 4)
        elements.append(
            SceneElement(
                id=f"{route_id}:junction:{index}",
                kind="junction",
                layer="wires",
                owner_id=route_id,
                parent_id=route_id,
                net_name=route.name,
                bounds=bounds,
                collision_bounds=bounds,
                hit_bounds=bounds.expanded(6),
                primitives=[RenderPrimitive(kind="circle", style_class="junction", geometry={"cx": x, "cy": y, "r": 4})],
                z_index=route_index * 100 + 90 + index,
                selectable=True,
            )
        )
    for index, (x, y, label) in enumerate(route.labels[:1]):
        elements.append(
            _text_element(
                element_id=f"{route_id}:inline-label:{index}",
                kind="net_label",
                layer="wires",
                text=label,
                x=x,
                y=y,
                anchor="start",
                bounds=text_box(label, x, y, "start"),
                parent_id=route_id,
                net_name=route.name,
                z_index=route_index * 100 + 95 + index,
                metadata={"text_role": "net_label", "attachment": False},
            )
        )
    route_bounds = _bounds_union([element.bounds for element in elements if element.parent_id == route_id]) or Bounds(min_x=0, min_y=0, max_x=0, max_y=0)
    return [
        SceneElement(
            id=route_id,
            kind="net_group",
            layer="wires",
            net_name=route.name,
            bounds=route_bounds,
            hit_bounds=route_bounds.expanded(8),
            selectable=True,
            z_index=route_index * 100,
            metadata={"render_style": route.render_style},
        ),
        *elements,
    ]


def _component_elements(ref: str, definition: ComponentDefinition, placement: Placement, component_index: int) -> list[SceneElement]:
    elements: list[SceneElement] = []
    group_id = f"component-{safe_id(ref)}"
    body_box = component_body_box(definition, placement)
    physical_symbol_box = symbol_box(definition, placement)
    width, height = component_size(definition, placement)
    body_bounds = Bounds.from_tuple(body_box)
    symbol_bounds = Bounds.from_tuple(physical_symbol_box)
    group_bounds = body_bounds.expanded(24)
    group_bounds = _bounds_union([group_bounds, symbol_bounds.expanded(10)]) or group_bounds
    elements.append(
        SceneElement(
            id=group_id,
            kind="component_group",
            layer="components",
            component_ref=ref,
            component_id=definition.id,
            bounds=group_bounds,
            collision_bounds=body_bounds,
            hit_bounds=body_bounds.expanded(24),
            selectable=True,
            z_index=component_index * 100,
            metadata={
                "renderer": definition.body.renderer,
                "category": definition.category,
                "rotation": placement.rotation,
                "placement": placement.model_dump(mode="json"),
            },
        )
    )
    elements.append(
        SceneElement(
            id=f"{group_id}:body",
            kind="component_body",
            layer="geometry",
            parent_id=group_id,
            component_ref=ref,
            component_id=definition.id,
            bounds=body_bounds,
            collision_bounds=body_bounds,
            hit_bounds=body_bounds.expanded(8),
            primitives=[RenderPrimitive(kind="rect", style_class=_body_css_class(definition), geometry={"x": placement.x, "y": placement.y, "width": width, "height": height, "rx": 6 if definition.body.renderer in {"dip_ic", "timer_555"} else 4})],
            z_index=component_index * 100 + 1,
            selectable=True,
        )
    )
    elements.append(
        SceneElement(
            id=f"{group_id}:symbol",
            kind="component_symbol",
            layer="geometry",
            parent_id=group_id,
            component_ref=ref,
            component_id=definition.id,
            bounds=symbol_bounds,
            collision_bounds=Bounds.from_tuple(inflate_box(physical_symbol_box, 10)),
            hit_bounds=symbol_bounds.expanded(8),
            primitives=component_symbol_primitives(definition, placement),
            z_index=component_index * 100 + 2,
            metadata={"renderer": definition.body.renderer},
        )
    )
    ref_x, ref_y, ref_anchor, value_x, value_y, value_anchor = component_label_positions(definition, placement)
    value = definition.name if not definition.part_number else definition.part_number
    elements.append(
        _text_element(
            element_id=f"{group_id}:ref-label",
            kind="component_reference_text",
            layer="geometry",
            text=ref,
            x=placement.x + ref_x,
            y=placement.y + ref_y,
            anchor=ref_anchor,
            bounds=text_box(ref, placement.x + ref_x, placement.y + ref_y, ref_anchor),
            parent_id=group_id,
            component_ref=ref,
            component_id=definition.id,
            z_index=component_index * 100 + 10,
            metadata={"text_role": "component_reference", "physical_wire_text_drc": True},
        )
    )
    elements.append(
        _text_element(
            element_id=f"{group_id}:value-label",
            kind="component_value_text",
            layer="geometry",
            text=value,
            x=placement.x + value_x,
            y=placement.y + value_y,
            anchor=value_anchor,
            bounds=text_box(value, placement.x + value_x, placement.y + value_y, value_anchor),
            parent_id=group_id,
            component_ref=ref,
            component_id=definition.id,
            z_index=component_index * 100 + 11,
            metadata={"text_role": "component_value", "physical_wire_text_drc": True},
        )
    )
    for pin_index, pin in enumerate(definition.pins):
        pin_x, pin_y = absolute_pin_point(definition, placement, pin)
        pin_bounds = Bounds(min_x=pin_x - 4, min_y=pin_y - 4, max_x=pin_x + 4, max_y=pin_y + 4)
        elements.append(
            SceneElement(
                id=f"{group_id}:pin:{safe_id(pin.number)}",
                kind="pin",
                layer="geometry",
                parent_id=group_id,
                component_ref=ref,
                component_id=definition.id,
                pin_name=pin.name,
                pin_number=pin.number,
                bounds=pin_bounds,
                collision_bounds=pin_bounds,
                hit_bounds=pin_bounds.expanded(8),
                primitives=[RenderPrimitive(kind="circle", style_class="pin-dot", geometry={"cx": pin_x, "cy": pin_y, "r": 4})],
                z_index=component_index * 100 + 20 + pin_index,
                selectable=True,
                metadata={"electrical_type": pin.electrical_type.value, "side": pin.side},
            )
        )
        label_x, number_y, number_anchor = pin_label_position(definition, placement, pin, "number")
        _, name_y, name_anchor = pin_label_position(definition, placement, pin, "name")
        abs_label_x = placement.x + label_x
        abs_number_y = placement.y + number_y
        abs_name_y = placement.y + name_y
        elements.append(
            _text_element(
                element_id=f"{group_id}:pin:{safe_id(pin.number)}:number",
                kind="pin_number_text",
                layer="geometry",
                text=pin.number,
                x=abs_label_x,
                y=abs_number_y,
                anchor=number_anchor,
                bounds=text_box(pin.number, abs_label_x, abs_number_y, number_anchor),
                parent_id=group_id,
                component_ref=ref,
                component_id=definition.id,
                pin_name=pin.name,
                pin_number=pin.number,
                z_index=component_index * 100 + 40 + pin_index,
                metadata={"text_role": "pin_number", "physical_wire_text_drc": False},
            )
        )
        elements.append(
            _text_element(
                element_id=f"{group_id}:pin:{safe_id(pin.number)}:name",
                kind="pin_name_text",
                layer="geometry",
                text=pin.name,
                x=abs_label_x,
                y=abs_name_y,
                anchor=name_anchor,
                bounds=text_box(pin.name, abs_label_x, abs_name_y, name_anchor),
                parent_id=group_id,
                component_ref=ref,
                component_id=definition.id,
                pin_name=pin.name,
                pin_number=pin.number,
                z_index=component_index * 100 + 60 + pin_index,
                metadata={"text_role": "pin_name", "physical_wire_text_drc": False},
            )
        )
    return elements


def _segment_element(
    element_id: str,
    kind: str,
    route: RoutedNet,
    segment: tuple[int, int, int, int],
    source_ref: str,
    parent_id: str,
    z_index: int,
    svg_id: str | None = None,
    wire_kind: str = "wire",
    metadata: dict[str, Any] | None = None,
) -> SceneElement:
    line = LineSegment.from_tuple(segment)
    bounds = line.bounds
    all_metadata = {
        "segment": segment,
        "wire_kind": wire_kind,
        "source_ref": source_ref,
        "svg_id": svg_id,
        "orientation": line.orientation,
        **(metadata or {}),
    }
    return SceneElement(
        id=element_id,
        kind=kind,
        layer="wires",
        owner_id=parent_id,
        parent_id=parent_id,
        net_name=route.name,
        bounds=bounds,
        hit_bounds=bounds.expanded(6),
        primitives=[RenderPrimitive(kind="line", style_class=_wire_css_class(wire_kind, metadata), geometry={"x1": segment[0], "y1": segment[1], "x2": segment[2], "y2": segment[3]})],
        z_index=z_index,
        selectable=True,
        metadata=all_metadata,
    )


def _text_element(
    element_id: str,
    kind: str,
    layer: str,
    text: str,
    x: float,
    y: float,
    anchor: str,
    bounds: tuple[float, float, float, float],
    parent_id: str | None,
    z_index: int,
    component_ref: str | None = None,
    component_id: str | None = None,
    pin_name: str | None = None,
    pin_number: str | None = None,
    net_name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> SceneElement:
    text_bounds = Bounds.from_tuple(bounds)
    return SceneElement(
        id=element_id,
        kind=kind,
        layer=layer,
        owner_id=parent_id,
        parent_id=parent_id,
        component_ref=component_ref,
        component_id=component_id,
        pin_name=pin_name,
        pin_number=pin_number,
        net_name=net_name,
        bounds=text_bounds,
        collision_bounds=text_bounds,
        hit_bounds=text_bounds.expanded(4),
        text=TextGeometry(text=text, origin=Point(x=x, y=y), anchor=anchor, bounds=text_bounds),
        primitives=[RenderPrimitive(kind="text", style_class=_text_css_class(kind), geometry={"x": x, "y": y, "anchor": anchor, "text": text})],
        z_index=z_index,
        metadata={"drc_wire_text": True, **(metadata or {})},
    )


def _text_css_class(kind: str) -> str:
    return {
        "component_reference_text": "ref-label",
        "component_value_text": "value-label",
        "pin_number_text": "pin-number",
        "pin_name_text": "pin-name",
        "ground_label": "net-label ground-label",
        "power_label": "net-label power-label",
        "net_label": "net-label",
    }.get(kind, "net-label")


def _wire_css_class(wire_kind: str, metadata: dict[str, Any] | None) -> str:
    if metadata and metadata.get("css_class"):
        return f"wire {metadata['css_class']}"
    if wire_kind == "label-stub":
        return "wire label-stub"
    return "wire"


def _body_css_class(definition: ComponentDefinition) -> str:
    if definition.body.renderer == "timer_555":
        return "body ic-body timer-555-body"
    if definition.body.renderer == "dip_ic":
        return "body ic-body"
    return "body"


def _bounds_union(bounds: list[Bounds]) -> Bounds | None:
    if not bounds:
        return None
    return Bounds(
        min_x=min(box.min_x for box in bounds),
        min_y=min(box.min_y for box in bounds),
        max_x=max(box.max_x for box in bounds),
        max_y=max(box.max_y for box in bounds),
    )


def _scene_canvas_bounds(layout: Layout, elements: list[SceneElement]) -> Bounds:
    width = int(layout.canvas.get("width", 1300))
    height = int(layout.canvas.get("height", 900))
    content = _bounds_union([element.bounds for element in elements if element.visible])
    if content is None:
        return Bounds(min_x=0, min_y=0, max_x=width, max_y=height)
    padding = 80
    return Bounds(
        min_x=0,
        min_y=0,
        max_x=max(width, content.max_x + padding),
        max_y=max(height, content.max_y + padding),
    )
