from pathlib import Path

from circuit_netlist.component_library import load_component_library
from circuit_netlist.geometry import (
    absolute_pin_point,
    boxes_overlap,
    component_body_box,
    component_size,
    inflate_box,
    segment_crosses_box as geometry_segment_crosses_box,
    segments_collinear_overlap,
    symbol_box,
)
from circuit_netlist.models import Circuit, ComponentInstance, Layout, Placement
from circuit_netlist.parser import NetlistParser
from circuit_netlist.placement import DeterministicPlacementEngine
from circuit_netlist.renderer import (
    LabelPlacementContext,
    PIN_ESCAPE_DISTANCE,
    choose_ground_symbol_attachment,
    choose_power_symbol_attachment,
    choose_net_label_position,
    power_symbol_box,
    render_circuit,
    text_box,
)
from circuit_netlist.router import ManhattanRouter
from circuit_netlist.scene_builder import build_schematic_scene


ROOT = Path(__file__).resolve().parents[1]
MIN_COMPONENT_GAP = 40


def load_example():
    library = load_component_library(ROOT / "components")
    circuit, diagnostics = NetlistParser().parse_file(ROOT / "examples" / "solar_led.cnet")
    assert circuit is not None, diagnostics
    layout = DeterministicPlacementEngine().place(circuit, library)
    routes = ManhattanRouter().route(circuit, library, layout)
    return library, circuit, layout, routes


def test_deterministic_placement_and_no_overlap() -> None:
    library, circuit, layout1, _ = load_example()
    layout2 = DeterministicPlacementEngine().place(circuit, library)
    assert layout1 == layout2
    boxes = []
    for instance in circuit.components:
        definition = library.get(instance.component_id)
        placement = layout1.components[instance.ref]
        boxes.append((instance.ref, *component_body_box(definition, placement)))
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert a[3] <= b[1] or b[3] <= a[1] or a[4] <= b[2] or b[4] <= a[2], (a, b)


def test_svg_output_contains_stable_ids_and_orthogonal_wires() -> None:
    library, circuit, layout, routes = load_example()
    svg = render_circuit(circuit, library, layout, routes, [])
    assert 'id="component-U1"' in svg
    assert 'id="pin-U1-1"' in svg
    assert 'id="net-VBAT"' in svg
    assert 'id="wire-' in svg
    assert 'data-scene-id=' in svg
    assert all(x1 == x2 or y1 == y2 for route in routes for x1, y1, x2, y2 in route.segments)
    assert 'power-symbol-GND' in svg
    assert 'power-symbol-VBAT' in svg
    assert 'net-label-BAT_SENSE' in svg


def test_power_symbols_and_led_render_on_pin_centerlines() -> None:
    library, circuit, layout, routes = load_example()
    svg = render_circuit(circuit, library, layout, routes, [])
    led_scene = build_schematic_scene(circuit, library, layout, routes)
    led_symbol = led_scene.first("component-LED1:symbol")
    assert led_symbol is not None
    led_x = layout.components["LED1"].x
    led_y = layout.components["LED1"].y
    assert any(primitive.kind == "line" and primitive.geometry == {"x1": led_x, "y1": led_y + 40, "x2": led_x + 50, "y2": led_y + 40} for primitive in led_symbol.primitives)
    assert any(primitive.kind == "circle" and primitive.geometry == {"cx": led_x, "cy": led_y + 40, "r": 5} for primitive in led_symbol.primitives)
    assert any(primitive.kind == "circle" and primitive.geometry == {"cx": led_x + 140, "cy": led_y + 40, "r": 5} for primitive in led_symbol.primitives)
    assert 'class="wire power-stub"' in svg
    assert 'class="wire power-stub ground-stub"' in svg
    assert 'class="net-label ground-label"' in svg
    assert 'class="power-shape power-flag"' in svg

    source = library.get("POWER_DC_SOURCE")
    source_circuit = Circuit(name="Source", components=[ComponentInstance(ref="V1", component_id="POWER_DC_SOURCE")], nets=[])
    source_svg = render_circuit(
        source_circuit,
        library,
        Layout(components={"V1": Placement(x=100, y=100)}, canvas={"width": 300, "height": 300}),
        [],
        [],
    )
    assert source.body.renderer == "dc_source"
    assert f'data-component-id="{source.id}"' in source_svg
    assert 'class="symbol" cx="150" cy="160" r="36"' in source_svg
    assert 'class="symbol" x1="150" y1="100" x2="150" y2="124"' in source_svg
    assert 'class="pin-contact" cx="150" cy="100" r="5"' in source_svg


def test_power_and_label_attachments_use_mandatory_pin_escape() -> None:
    library, circuit, layout, routes = load_example()
    context = LabelPlacementContext(circuit, library, layout, routes)
    for route in routes:
        if route.render_style == "power_symbol":
            for endpoint in route.endpoints:
                x = int(endpoint["x"])
                y = int(endpoint["y"])
                side = str(endpoint["side"])
                if route.name == "GND":
                    segments, symbol_x, symbol_y = choose_ground_symbol_attachment(x, y, side, context)
                    kind = "ground"
                    assert symbol_y > segments[0][3]
                else:
                    segments, symbol_x, symbol_y = choose_power_symbol_attachment(route.name, x, y, side, context)
                    kind = "power"
                    assert symbol_y < segments[0][3]
                if side in {"top", "bottom"}:
                    assert segments[0][0] == segments[0][2] == x
                else:
                    assert power_symbol_has_visible_escape(segments, side)
                assert symbol_does_not_touch_any_body(power_symbol_box(symbol_x, symbol_y, route.name, kind), circuit, library, layout)
                assert path_does_not_cross_text(segments, context)
        if route.render_style == "net_label":
            for endpoint in route.endpoints:
                segments, *_ = choose_net_label_position(route.name, int(endpoint["x"]), int(endpoint["y"]), str(endpoint["side"]), context)
                assert escape_direction_matches_side(segments[0], str(endpoint["side"]))
                assert path_does_not_cross_text(segments, context)


def test_chg1_vbat_and_gnd_use_l_shaped_breakouts() -> None:
    _, _, _, routes = load_example()
    by_name = {route.name: route for route in routes}
    vbat = next(endpoint for endpoint in by_name["VBAT"].endpoints if endpoint["component_ref"] == "CHG1" and endpoint["pin_name"] == "BAT")
    gnd = next(endpoint for endpoint in by_name["GND"].endpoints if endpoint["component_ref"] == "CHG1" and endpoint["pin_name"] == "GND")

    vbat_segments, vbat_x, vbat_y = choose_power_symbol_attachment("VBAT", int(vbat["x"]), int(vbat["y"]), str(vbat["side"]), None)
    assert str(vbat["side"]) == "right"
    assert escape_direction_matches_side(vbat_segments[0], "right")
    assert len(vbat_segments) >= 2
    assert vbat_segments[1][0] == vbat_segments[0][2] == vbat_x
    assert vbat_y < int(vbat["y"])

    gnd_segments, gnd_x, gnd_y = choose_ground_symbol_attachment(int(gnd["x"]), int(gnd["y"]), str(gnd["side"]), None)
    assert str(gnd["side"]) == "left"
    assert escape_direction_matches_side(gnd_segments[0], "left")
    assert len(gnd_segments) >= 2
    assert gnd_segments[1][0] == gnd_segments[0][2] == gnd_x
    assert gnd_y > int(gnd["y"])


def test_bottom_side_gnd_pins_drop_straight_down() -> None:
    _, _, _, routes = load_example()
    gnd = next(route for route in routes if route.name == "GND")
    bottom_endpoints = [endpoint for endpoint in gnd.endpoints if endpoint["side"] == "bottom"]
    assert bottom_endpoints
    for endpoint in bottom_endpoints:
        segments, symbol_x, symbol_y = choose_ground_symbol_attachment(int(endpoint["x"]), int(endpoint["y"]), str(endpoint["side"]), None)
        assert segments == [(int(endpoint["x"]), int(endpoint["y"]), int(endpoint["x"]), symbol_y - 12)]
        assert symbol_x == int(endpoint["x"])
        assert symbol_y > int(endpoint["y"])


def test_top_bottom_power_pins_use_straight_vertical_symbols() -> None:
    _, _, _, routes = load_example()
    vbat = next(route for route in routes if route.name == "VBAT")
    vertical_endpoints = [endpoint for endpoint in vbat.endpoints if endpoint["side"] in {"top", "bottom"}]
    assert vertical_endpoints
    for endpoint in vertical_endpoints:
        segments, symbol_x, symbol_y = choose_power_symbol_attachment("VBAT", int(endpoint["x"]), int(endpoint["y"]), str(endpoint["side"]), None)
        assert len(segments) == 1
        assert segments[0][0] == segments[0][2] == int(endpoint["x"])
        assert symbol_x == int(endpoint["x"])
        assert symbol_y < int(endpoint["y"])


def test_routed_wires_do_not_cross_component_bodies() -> None:
    library, circuit, layout, routes = load_example()
    boxes = []
    for instance in circuit.components:
        definition = library.get(instance.component_id)
        placement = layout.components[instance.ref]
        boxes.append(component_body_box(definition, placement))
    for route in routes:
        for segment in route.segments:
            assert not any(segment_crosses_box(segment, box) for box in boxes), (route.name, segment)


def test_led_wires_terminate_on_led_pin_contacts() -> None:
    library, circuit, layout, routes = load_example()
    led_definition = library.get("LIGHT_LED_WHITE")
    led_placement = layout.components["LED1"]
    led_pins = {pin.name: absolute_pin_point(led_definition, led_placement, pin) for pin in led_definition.pins}
    route_points = {
        route.name: {(x1, y1) for x1, y1, _, _ in route.segments} | {(x2, y2) for _, _, x2, y2 in route.segments}
        for route in routes
    }
    assert led_pins["A"] in route_points["LED_ANODE"]
    assert led_pins["K"] in route_points["LED_SWITCH"]


def test_mosfet_wires_terminate_on_mosfet_pin_contacts() -> None:
    library, circuit, layout, routes = load_example()
    scene = build_schematic_scene(circuit, library, layout, routes)
    q1_symbol = scene.first("component-Q1:symbol")
    assert q1_symbol is not None
    q1_x = layout.components["Q1"].x
    q1_y = layout.components["Q1"].y
    assert any(primitive.kind == "circle" and primitive.geometry == {"cx": q1_x, "cy": q1_y + 40, "r": 5} for primitive in q1_symbol.primitives)
    assert any(primitive.kind == "circle" and primitive.geometry == {"cx": q1_x + 80, "cy": q1_y, "r": 5} for primitive in q1_symbol.primitives)
    assert any(primitive.kind == "circle" and primitive.geometry == {"cx": q1_x + 80, "cy": q1_y + 120, "r": 5} for primitive in q1_symbol.primitives)

    q1_definition = library.get("BASIC_NMOS")
    q1_placement = layout.components["Q1"]
    q1_pins = {pin.name: absolute_pin_point(q1_definition, q1_placement, pin) for pin in q1_definition.pins}
    route_points = {
        route.name: {(x1, y1) for x1, y1, _, _ in route.segments} | {(x2, y2) for _, _, x2, y2 in route.segments} | {(int(endpoint["x"]), int(endpoint["y"])) for endpoint in route.endpoints}
        for route in routes
    }
    assert q1_pins["G"] in route_points["MOSFET_GATE"]
    assert q1_pins["D"] in route_points["LED_SWITCH"]
    assert q1_pins["S"] in route_points["GND"]


def test_mosfet_symbol_is_conventional_enhancement_mode() -> None:
    library, circuit, layout, routes = load_example()
    svg = render_circuit(circuit, library, layout, routes, [])
    start = svg.index('id="component-Q1"')
    end = svg.index('id="component-LED1"', start)
    q1_svg = svg[start:end]
    assert 'data-component-id="BASIC_NMOS"' in q1_svg
    assert '<circle class="symbol" cx="80" cy="60"' not in q1_svg
    assert 'data-symbol-style="compact_no_bulk"' in q1_svg
    assert 'data-mosfet-mode="enhancement"' in q1_svg
    assert 'class="symbol mosfet-gate"' in q1_svg
    assert 'class="symbol mosfet-channel enhancement-channel"' in q1_svg
    assert 'class="symbol mosfet-body-diode nmos-body-diode"' not in q1_svg
    assert 'class="symbol mosfet-arrow nmos-arrow"' in q1_svg
    assert 'd="M 64 84 L 76 84 M 71 79 L 76 84 L 71 89"' in q1_svg
    assert 'class="symbol-fill mosfet-diode-triangle"' not in q1_svg
    assert 'data-pin-number="2" data-pin-name="D"' in q1_svg
    assert 'data-pin-number="3" data-pin-name="S"' in q1_svg


def test_pmos_compact_symbol_has_source_on_top_and_arrow_in() -> None:
    library = load_component_library(ROOT / "components")
    circuit = Circuit(name="Pmos", components=[ComponentInstance(ref="Q1", component_id="BASIC_PMOS")], nets=[])
    svg = render_circuit(circuit, library, Layout(components={"Q1": Placement(x=100, y=100)}), [], [])
    assert 'data-component-id="BASIC_PMOS"' in svg
    assert 'data-symbol-style="compact_no_bulk"' in svg
    assert 'class="symbol mosfet-arrow pmos-arrow"' in svg
    assert 'd="M 78 36 L 66 36 M 71 31 L 66 36 L 71 41"' in svg
    assert 'class="symbol mosfet-body-diode pmos-body-diode"' not in svg
    assert 'data-pin-number="3" data-pin-name="S"' in svg
    assert 'data-pin-number="2" data-pin-name="D"' in svg


def test_555_timer_symbol_renders_named_astable_body_and_standard_pins() -> None:
    library = load_component_library(ROOT / "components")
    circuit = Circuit(name="Timer", components=[ComponentInstance(ref="U1", component_id="BASIC_555_TIMER")], nets=[])
    svg = render_circuit(circuit, library, Layout(components={"U1": Placement(x=100, y=100)}), [], [])
    assert 'data-component-id="BASIC_555_TIMER"' in svg
    assert 'class="body ic-body timer-555-body"' in svg
    assert 'class="timer-title" x="190" y="202" text-anchor="middle"' in svg
    assert 'class="timer-subtitle" x="190" y="228" text-anchor="middle"' in svg
    for number, name in [("1", "GND"), ("2", "TRIG"), ("3", "OUT"), ("4", "RESET"), ("5", "CTRL"), ("6", "THRESH"), ("7", "DISCH"), ("8", "VCC")]:
        assert f'data-pin-number="{number}" data-pin-name="{name}"' in svg


def test_wires_connect_to_exact_pin_coordinates() -> None:
    library, circuit, layout, routes = load_example()
    endpoints_by_net = {
        route.name: (
            {(x1, y1) for x1, y1, _, _ in route.segments}
            | {(x2, y2) for _, _, x2, y2 in route.segments}
            | {(int(endpoint["x"]), int(endpoint["y"])) for endpoint in route.endpoints}
        )
        for route in routes
    }
    ref_to_id = {component.ref: component.component_id for component in circuit.components}
    for net in circuit.nets:
        endpoints = endpoints_by_net[net.name]
        for pinref in net.pins:
            definition = library.get(ref_to_id[pinref.component_ref])
            placement = layout.components[pinref.component_ref]
            pin = definition.resolve_pin(pinref.pin_name)
            assert absolute_pin_point(definition, placement, pin) in endpoints, (net.name, pinref.component_ref, pin.name)


def test_default_net_rendering_policy() -> None:
    _, _, _, routes = load_example()
    by_name = {route.name: route for route in routes}
    assert by_name["GND"].render_style == "power_symbol"
    assert by_name["VBAT"].render_style == "power_symbol"
    assert by_name["BAT_SENSE"].render_style == "net_label"
    assert by_name["LED_SWITCH"].render_style == "local_wire"
    assert by_name["MOSFET_GATE"].render_style == "local_wire"
    assert not by_name["GND"].segments
    assert not by_name["VBAT"].segments
    assert by_name["MOSFET_GATE"].segments


def test_example_has_fewer_physical_wire_segments() -> None:
    _, _, _, routes = load_example()
    routed_nets = [route for route in routes if route.segments]
    assert len(routed_nets) < 6
    assert sum(len(route.segments) for route in routes) < 60


def test_solar_led_local_routes_stay_simple() -> None:
    _, _, _, routes = load_example()
    by_name = {route.name: route for route in routes}
    for name in ("LED_ANODE",):
        route = by_name[name]
        assert len(route.segments) == 1, (name, route.segments)
        x1, y1, x2, y2 = route.segments[0]
        direct = abs(x1 - x2) + abs(y1 - y2)
        assert route_length(route.segments) <= direct * 2
    assert len(by_name["MOSFET_GATE"].segments) <= 10
    assert all(x1 == x2 or y1 == y2 for x1, y1, x2, y2 in by_name["MOSFET_GATE"].segments)
    led_switch = by_name["LED_SWITCH"]
    assert len(led_switch.segments) <= 4
    assert all(x1 == x2 or y1 == y2 for x1, y1, x2, y2 in led_switch.segments)


def test_solar_led_layout_keeps_component_spacing_and_groups_dividers() -> None:
    library, circuit, layout, _ = load_example()
    boxes = {}
    for instance in circuit.components:
        definition = library.get(instance.component_id)
        placement = layout.components[instance.ref]
        boxes[instance.ref] = component_body_box(definition, placement)

    for ref_a, box_a in boxes.items():
        for ref_b, box_b in boxes.items():
            if ref_a >= ref_b:
                continue
            assert component_gap(box_a, box_b) >= MIN_COMPONENT_GAP or not separated_on_both_axes(box_a, box_b), (ref_a, ref_b)

    for ref in ("R_SUN_TOP", "R_SUN_BOT", "R_BAT_TOP", "R_BAT_BOT"):
        assert layout.components[ref].rotation == 90
    assert layout.components["R_SUN_TOP"].x == layout.components["R_SUN_BOT"].x
    assert layout.components["R_SUN_TOP"].y < layout.components["R_SUN_BOT"].y
    assert layout.components["R_BAT_TOP"].x == layout.components["R_BAT_BOT"].x
    assert layout.components["R_BAT_TOP"].y < layout.components["R_BAT_BOT"].y


def test_label_placement_avoids_text_body_and_wire_collisions() -> None:
    library, circuit, layout, routes = load_example()
    context = LabelPlacementContext(circuit, library, layout, routes)
    text_boxes = list(context.text_boxes)
    body_boxes = list(context.body_boxes)
    svg = render_circuit(circuit, library, layout, routes, [])
    assert "net-label-BAT_SENSE" in svg

    label_context = LabelPlacementContext(circuit, library, layout, routes)
    for route in routes:
        if route.render_style not in {"net_label", "power_symbol"}:
            continue
        for endpoint in route.endpoints:
            if route.render_style == "power_symbol" and route.name == "GND":
                continue
            x = int(endpoint["x"])
            y = int(endpoint["y"])
            if route.render_style == "power_symbol":
                segments, symbol_x, symbol_y = choose_power_symbol_attachment(route.name, x, y, str(endpoint["side"]), label_context)
                if str(endpoint["side"]) in {"top", "bottom"}:
                    assert segments[0][0] == segments[0][2] == x
                else:
                    assert power_symbol_has_visible_escape(segments, str(endpoint["side"]))
                label_y = symbol_y - 24
                box = text_box(route.name, symbol_x, label_y, "middle")
            else:
                from circuit_netlist.renderer import choose_net_label_position

                segments, _, _, anchor, label_x, label_y, _ = choose_net_label_position(route.name, x, y, str(endpoint["side"]), label_context)
                assert escape_direction_matches_side(segments[0], str(endpoint["side"]))
                box = text_box(route.name, label_x, label_y, anchor)
            assert not any(boxes_overlap(box, existing) for existing in text_boxes)
            if route.render_style == "net_label":
                assert not any(boxes_overlap(box, body) for body in body_boxes)
            if route.render_style == "power_symbol":
                label_context.reserve_text(route.name, symbol_x, label_y, "middle")
                for segment in segments:
                    label_context.reserve_stub(segment)
            else:
                label_context.reserve_text(route.name, label_x, label_y, anchor)
                for segment in segments:
                    label_context.reserve_stub(segment)
            text_boxes.append(box)


def test_orientation_selection_matches_schematic_roles() -> None:
    library, _, layout, _ = load_example()
    for ref in ("R_SUN_TOP", "R_SUN_BOT", "R_BAT_TOP", "R_BAT_BOT"):
        assert layout.components[ref].rotation == 90
        assert component_size(library.get("BASIC_RESISTOR"), layout.components[ref]) == (80, 140)

    assert layout.components["R_PULL"].rotation == 90
    assert layout.components["C1"].rotation == 90
    assert layout.components["R_GATE"].rotation == 0
    assert layout.components["R_LED"].rotation == 0


def test_decoupling_capacitor_is_vertical_and_near_mcu() -> None:
    _, _, layout, _ = load_example()
    c1 = layout.components["C1"]
    u1 = layout.components["U1"]
    assert c1.rotation == 90
    assert abs((c1.x + 40) - u1.x) <= 220
    assert c1.y > u1.y


def test_wire_symbol_and_text_overlap_helpers_detect_collisions() -> None:
    library, circuit, layout, routes = load_example()
    ref_to_instance = {component.ref: component for component in circuit.components}
    r_pull = ref_to_instance["R_PULL"]
    r_pull_definition = library.get(r_pull.component_id)
    r_pull_placement = layout.components["R_PULL"]
    x_mid = r_pull_placement.x + 40
    assert geometry_segment_crosses_box((x_mid, r_pull_placement.y - 20, x_mid, r_pull_placement.y + 160), symbol_box(r_pull_definition, r_pull_placement))
    assert geometry_segment_crosses_box((540, 410, 620, 410), text_box("U1", 540, 410, "start"))

    by_name = {route.name: route for route in routes}
    q1_source = next(endpoint for endpoint in by_name["GND"].endpoints if endpoint["component_ref"] == "Q1")
    q1_definition = library.get("BASIC_NMOS")
    q1_placement = layout.components["Q1"]
    source_pin = next(pin for pin in q1_definition.pins if pin.name == "S")
    assert (int(q1_source["x"]), int(q1_source["y"])) == absolute_pin_point(q1_definition, q1_placement, source_pin)
    assert not power_stub_hits_symbol("GND", "Q1", "R_PULL", library, circuit, layout, routes)


def test_collinear_wire_overlap_detection() -> None:
    assert segments_collinear_overlap((0, 20, 100, 20), (40, 20, 140, 20))
    assert segments_collinear_overlap((20, 0, 20, 100), (20, 40, 20, 140))
    assert not segments_collinear_overlap((0, 20, 100, 20), (40, 30, 140, 30))


def test_example_layout_has_no_hard_geometry_collisions() -> None:
    library, circuit, layout, routes = load_example()
    body_boxes = []
    text_boxes = []
    symbol_boxes = []
    pin_points_by_ref = {}
    for instance in circuit.components:
        definition = library.get(instance.component_id)
        placement = layout.components[instance.ref]
        body_boxes.append((instance.ref, component_body_box(definition, placement)))
        symbol_boxes.append((instance.ref, symbol_box(definition, placement)))
        pin_points_by_ref[instance.ref] = {absolute_pin_point(definition, placement, pin) for pin in definition.pins}
        width, height = component_size(definition, placement)
        text_boxes.append((instance.ref, text_box(instance.ref, placement.x, placement.y - 10, "start")))
        text_boxes.append((instance.ref, text_box(definition.name if not definition.part_number else definition.part_number, placement.x, placement.y + height + 18, "start")))

    for index, (ref, box) in enumerate(body_boxes):
        for other_ref, other_box in body_boxes[index + 1:]:
            assert not boxes_overlap(box, other_box), (ref, other_ref)

    for route in routes:
        for segment in route.segments:
            for ref, box in symbol_boxes:
                if segment_touches_component_pin(segment, pin_points_by_ref[ref]):
                    continue
                assert not geometry_segment_crosses_box(segment, inflate_box(box, 10)), (route.name, segment, ref)
            for ref, box in text_boxes:
                assert not geometry_segment_crosses_box(segment, inflate_box(box, 8)), (route.name, segment, ref)

    routed_segments = [(route.name, segment) for route in routes for segment in route.segments]
    for index, (net_name, segment) in enumerate(routed_segments):
        for other_net, other_segment in routed_segments[index + 1:]:
            if net_name != other_net:
                assert not segments_collinear_overlap(segment, other_segment), (net_name, segment, other_net, other_segment)

    for instance in circuit.components:
        for route in routes:
            if route.render_style != "power_symbol":
                continue
            for endpoint in route.endpoints:
                if endpoint["component_ref"] == instance.ref:
                    continue
                assert not power_stub_hits_symbol(route.name, str(endpoint["component_ref"]), instance.ref, library, circuit, layout, routes)

    for route in routes:
        if route.render_style != "net_label":
            continue
        for endpoint in route.endpoints:
            for instance in circuit.components:
                if endpoint["component_ref"] == instance.ref:
                    continue
                assert not label_stub_hits_symbol(route.name, endpoint, instance.ref, library, circuit, layout, routes)


def segment_crosses_box(segment: tuple[int, int, int, int], box: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = segment
    bx1, by1, bx2, by2 = box
    if y1 == y2:
        if by1 < y1 < by2:
            return max(min(x1, x2), bx1) < min(max(x1, x2), bx2)
        return False
    if x1 == x2:
        if bx1 < x1 < bx2:
            return max(min(y1, y2), by1) < min(max(y1, y2), by2)
        return False
    return False


def route_length(segments: list[tuple[int, int, int, int]]) -> int:
    return sum(abs(x1 - x2) + abs(y1 - y2) for x1, y1, x2, y2 in segments)


def component_gap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    horizontal = max(b[0] - a[2], a[0] - b[2], 0)
    vertical = max(b[1] - a[3], a[1] - b[3], 0)
    return max(horizontal, vertical)


def separated_on_both_axes(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]


def segment_touches_component_pin(segment: tuple[int, int, int, int], pins: set[tuple[int, int]]) -> bool:
    return (segment[0], segment[1]) in pins or (segment[2], segment[3]) in pins


def escape_direction_matches_side(segment: tuple[int, int, int, int], side: str) -> bool:
    x1, y1, x2, y2 = segment
    min_escape = 24
    if side == "left":
        return y1 == y2 and x2 <= x1 - min_escape
    if side == "right":
        return y1 == y2 and x2 >= x1 + min_escape
    if side == "top":
        return x1 == x2 and y2 <= y1 - min_escape
    if side == "bottom":
        return x1 == x2 and y2 >= y1 + min_escape
    return False


def power_symbol_escape_is_visible(segment: tuple[int, int, int, int], side: str) -> bool:
    x1, y1, x2, y2 = segment
    min_escape = 24
    if side in {"top", "bottom"}:
        return y1 == y2 and abs(x2 - x1) >= min_escape
    return escape_direction_matches_side(segment, side)


def power_symbol_has_visible_escape(segments: list[tuple[int, int, int, int]], side: str) -> bool:
    if side in {"top", "bottom"}:
        return len(segments) >= 3 and power_symbol_escape_is_visible(segments[1], side)
    return bool(segments) and power_symbol_escape_is_visible(segments[0], side)


def symbol_does_not_touch_any_body(box, circuit, library, layout) -> bool:
    padded = inflate_box(box, 2)
    for instance in circuit.components:
        definition = library.get(instance.component_id)
        if boxes_overlap(padded, component_body_box(definition, layout.components[instance.ref])):
            return False
    return True


def path_does_not_cross_text(segments: list[tuple[int, int, int, int]], context: LabelPlacementContext) -> bool:
    return not any(context.segment_collides_with_text(segment) for segment in segments)


def power_stub_hits_symbol(net_name, source_ref, target_ref, library, circuit, layout, routes) -> bool:
    from circuit_netlist.renderer import LabelPlacementContext

    route = next(route for route in routes if route.name == net_name)
    endpoint = next(endpoint for endpoint in route.endpoints if endpoint["component_ref"] == source_ref)
    x = int(endpoint["x"])
    y = int(endpoint["y"])
    side = str(endpoint.get("side", "right"))
    context = LabelPlacementContext(circuit, library, layout, routes)
    if net_name == "GND":
        stubs, _, _ = choose_ground_symbol_attachment(x, y, side, context)
    else:
        stubs, _, _ = choose_power_symbol_attachment(net_name, x, y, side, context)
    target = next(component for component in circuit.components if component.ref == target_ref)
    definition = library.get(target.component_id)
    box = inflate_box(symbol_box(definition, layout.components[target_ref]), 10)
    return any(geometry_segment_crosses_box(stub, box) for stub in stubs)


def label_stub_hits_symbol(net_name, endpoint, target_ref, library, circuit, layout, routes) -> bool:
    from circuit_netlist.renderer import LabelPlacementContext, choose_net_label_position

    x = int(endpoint["x"])
    y = int(endpoint["y"])
    context = LabelPlacementContext(circuit, library, layout, routes)
    stubs, *_ = choose_net_label_position(net_name, x, y, str(endpoint["side"]), context)
    target = next(component for component in circuit.components if component.ref == target_ref)
    definition = library.get(target.component_id)
    box = inflate_box(symbol_box(definition, layout.components[target_ref]), 10)
    return any(geometry_segment_crosses_box(stub, box) for stub in stubs)
