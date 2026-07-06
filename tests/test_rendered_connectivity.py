from __future__ import annotations

from pathlib import Path

from circuit_netlist.component_library import load_component_library
from circuit_netlist.models import Circuit, ComponentInstance, Layout, Net, PinRef
from circuit_netlist.models import Diagnostic, RenderQualityMode, Severity
from circuit_netlist.parser import NetlistParser
from circuit_netlist.placement import DeterministicPlacementEngine
from circuit_netlist.rendered_connectivity import rendered_connectivity_graph, validate_rendered_connectivity
from circuit_netlist.router import ManhattanRouter
from circuit_netlist.scene import Bounds, RenderPrimitive, SceneElement, SchematicScene, TextGeometry, Point
from circuit_netlist.scene_builder import build_schematic_scene


ROOT = Path(__file__).resolve().parents[1]


def pinref(ref: str, name: str, number: str | None = None) -> PinRef:
    return PinRef(component_ref=ref, pin_name=name, resolved_name=name, resolved_number=number)


def circuit_with(nets: list[Net]) -> Circuit:
    refs = sorted({pin.component_ref for net in nets for pin in net.pins})
    return Circuit(name="RenderedConnectivity", components=[ComponentInstance(ref=ref, component_id="BASIC_TEST_POINT") for ref in refs], nets=nets)


def scene_with(elements: list[SceneElement]) -> SchematicScene:
    return SchematicScene(circuit_name="RenderedConnectivity", canvas_bounds=Bounds(min_x=0, min_y=0, max_x=500, max_y=500), elements=elements)


def net_group(net: str, style: str = "local_wire") -> SceneElement:
    return SceneElement(
        id=f"net-{net}",
        kind="net_group",
        layer="wires",
        net_name=net,
        bounds=Bounds(min_x=0, min_y=0, max_x=0, max_y=0),
        metadata={"render_style": style},
    )


def pin_element(ref: str, name: str, x: int, y: int, number: str | None = None) -> SceneElement:
    bounds = Bounds(min_x=x - 4, min_y=y - 4, max_x=x + 4, max_y=y + 4)
    return SceneElement(
        id=f"pin-{ref}-{name}",
        kind="pin",
        layer="geometry",
        component_ref=ref,
        pin_name=name,
        pin_number=number,
        bounds=bounds,
        collision_bounds=bounds,
        primitives=[RenderPrimitive(kind="circle", geometry={"cx": x, "cy": y, "r": 4})],
        metadata={"role": "pin", "connectivity_anchor": True},
    )


def segment_element(element_id: str, segment: tuple[int, int, int, int], *, net: str, kind: str = "wire", parent: str | None = None, wire_kind: str = "wire") -> SceneElement:
    x1, y1, x2, y2 = segment
    bounds = Bounds(min_x=min(x1, x2), min_y=min(y1, y2), max_x=max(x1, x2), max_y=max(y1, y2))
    return SceneElement(
        id=element_id,
        kind=kind,
        layer="wires",
        parent_id=parent or f"net-{net}",
        owner_id=parent or f"net-{net}",
        net_name=net,
        bounds=bounds,
        primitives=[RenderPrimitive(kind="line", geometry={"x1": x1, "y1": y1, "x2": x2, "y2": y2})],
        metadata={"segment": segment, "wire_kind": wire_kind, "role": "stub" if kind == "wire_stub" else "wire"},
    )


def box_element(element_id: str, box: tuple[int, int, int, int], *, net: str, kind: str, parent: str | None = None) -> SceneElement:
    bounds = Bounds.from_tuple(box)
    return SceneElement(
        id=element_id,
        kind=kind,
        layer="wires",
        parent_id=parent or f"net-{net}",
        owner_id=parent or f"net-{net}",
        net_name=net,
        bounds=bounds,
        collision_bounds=bounds,
        primitives=[RenderPrimitive(kind="rect", geometry={"x": box[0], "y": box[1], "width": box[2] - box[0], "height": box[3] - box[1]})],
        metadata={"role": kind, "connectivity_anchor": True},
    )


def text_element(element_id: str, text: str, box: tuple[int, int, int, int], *, net: str, kind: str = "net_label") -> SceneElement:
    bounds = Bounds.from_tuple(box)
    return SceneElement(
        id=element_id,
        kind=kind,
        layer="wires",
        parent_id=f"net-{net}",
        owner_id=f"net-{net}",
        net_name=net,
        bounds=bounds,
        collision_bounds=bounds,
        text=TextGeometry(text=text, origin=Point(x=box[0], y=box[3]), anchor="start", bounds=bounds),
        primitives=[RenderPrimitive(kind="text", geometry={"x": box[0], "y": box[3], "text": text})],
        metadata={"text_role": kind, "attachment": True},
    )


def codes_for(circuit: Circuit, elements: list[SceneElement]) -> list[str]:
    diagnostics, _ = validate_rendered_connectivity(circuit, scene_with(elements))
    return sorted(diag.code for diag in diagnostics)


def test_rendered_connectivity_module_exposes_graph_model() -> None:
    circuit = circuit_with([Net(name="A", pins=[pinref("U1", "A"), pinref("U2", "B")])])
    graph, diagnostics = rendered_connectivity_graph(
        circuit,
        scene_with(
            [
                net_group("A"),
                pin_element("U1", "A", 20, 20),
                pin_element("U2", "B", 120, 20),
                segment_element("wire-A", (20, 20, 120, 20), net="A"),
            ]
        ),
    )
    assert diagnostics == []
    assert graph.nodes
    assert any(edge.kind == "wire_continuity" for edge in graph.edges)


def test_continuous_direct_two_pin_net_passes() -> None:
    circuit = circuit_with([Net(name="A", pins=[pinref("U1", "A"), pinref("U2", "B")])])
    assert codes_for(circuit, [net_group("A"), pin_element("U1", "A", 20, 20), pin_element("U2", "B", 120, 20), segment_element("wire-A", (20, 20, 120, 20), net="A")]) == []


def test_direct_wire_gap_detects_unreached_pin_and_open() -> None:
    circuit = circuit_with([Net(name="A", pins=[pinref("U1", "A"), pinref("U2", "B")])])
    codes = codes_for(circuit, [net_group("A"), pin_element("U1", "A", 20, 20), pin_element("U2", "B", 120, 20), segment_element("wire-A", (20, 20, 110, 20), net="A")])
    assert "ROUTING_PIN_UNREACHED" in codes
    assert "DRC_RENDERED_NET_OPEN" in codes


def test_direct_net_with_disconnected_islands_is_detected() -> None:
    circuit = circuit_with([Net(name="A", pins=[pinref("U1", "A"), pinref("U2", "B")])])
    codes = codes_for(
        circuit,
        [
            net_group("A"),
            pin_element("U1", "A", 20, 20),
            pin_element("U2", "B", 160, 20),
            segment_element("wire-A-1", (20, 20, 80, 20), net="A"),
            segment_element("wire-A-2", (120, 20, 160, 20), net="A"),
        ],
    )
    assert "ROUTING_NET_HAS_MULTIPLE_ISLANDS" in codes
    assert "ROUTING_NET_INCOMPLETE" in codes


def test_valid_label_equivalent_net_passes() -> None:
    circuit = circuit_with([Net(name="LABEL_NET", pins=[pinref("U1", "A"), pinref("U2", "B")])])
    assert (
        codes_for(
            circuit,
            [
                net_group("LABEL_NET", "net_label"),
                pin_element("U1", "A", 20, 20),
                pin_element("U2", "B", 220, 20),
                box_element("label-1", (70, 14, 120, 26), net="LABEL_NET", kind="net_label_endpoint"),
                segment_element("stub-1", (20, 20, 70, 20), net="LABEL_NET", kind="wire_stub", wire_kind="label-stub"),
                box_element("label-2", (170, 14, 220, 26), net="LABEL_NET", kind="net_label_endpoint"),
                segment_element("stub-2", (220, 20, 170, 20), net="LABEL_NET", kind="wire_stub", wire_kind="label-stub"),
            ],
        )
        == []
    )


def test_label_without_stub_is_detected() -> None:
    circuit = circuit_with([Net(name="LABEL_NET", pins=[pinref("U1", "A")])])
    codes = codes_for(circuit, [net_group("LABEL_NET", "net_label"), pin_element("U1", "A", 20, 20), box_element("label-1", (70, 14, 120, 26), net="LABEL_NET", kind="net_label_endpoint")])
    assert "ROUTING_LABEL_WITHOUT_STUB" in codes
    assert "ROUTING_PIN_UNREACHED" in codes


def test_stub_gap_before_label_anchor_is_detected() -> None:
    circuit = circuit_with([Net(name="LABEL_NET", pins=[pinref("U1", "A")])])
    codes = codes_for(
        circuit,
        [
            net_group("LABEL_NET", "net_label"),
            pin_element("U1", "A", 20, 20),
            box_element("label-1", (100, 14, 150, 26), net="LABEL_NET", kind="net_label_endpoint"),
            segment_element("stub-1", (20, 20, 94, 20), net="LABEL_NET", kind="wire_stub", wire_kind="label-stub"),
        ],
    )
    assert "ROUTING_LABEL_WITHOUT_STUB" in codes
    assert "ROUTING_STUB_DISCONNECTED" in codes


def test_valid_power_symbol_equivalent_net_passes() -> None:
    circuit = circuit_with([Net(name="GND", pins=[pinref("U1", "GND"), pinref("U2", "GND")])])
    assert (
        codes_for(
            circuit,
            [
                net_group("GND", "power_symbol"),
                pin_element("U1", "GND", 20, 80),
                pin_element("U2", "GND", 220, 80),
                box_element("gnd-1", (70, 70, 110, 110), net="GND", kind="ground_symbol"),
                segment_element("stub-1", (20, 80, 70, 80), net="GND", kind="wire_stub", wire_kind="power-stub"),
                box_element("gnd-2", (170, 70, 210, 110), net="GND", kind="ground_symbol"),
                segment_element("stub-2", (220, 80, 210, 80), net="GND", kind="wire_stub", wire_kind="power-stub"),
            ],
        )
        == []
    )


def test_power_symbol_disconnected_from_stub_is_detected() -> None:
    circuit = circuit_with([Net(name="VBAT", pins=[pinref("U1", "VBAT")])])
    codes = codes_for(
        circuit,
        [
            net_group("VBAT", "power_symbol"),
            pin_element("U1", "VBAT", 20, 80),
            box_element("vbat-1", (100, 70, 140, 110), net="VBAT", kind="power_symbol"),
            segment_element("stub-1", (20, 80, 90, 80), net="VBAT", kind="wire_stub", wire_kind="power-stub"),
        ],
    )
    assert "ROUTING_POWER_SYMBOL_WITHOUT_STUB" in codes
    assert "ROUTING_STUB_DISCONNECTED" in codes


def test_orphan_route_segment_is_detected() -> None:
    circuit = circuit_with([Net(name="A", pins=[pinref("U1", "A")])])
    codes = codes_for(circuit, [net_group("A"), pin_element("U1", "A", 20, 20), segment_element("wire-A", (100, 100, 160, 100), net="A")])
    assert "ROUTING_DANGLING_SEGMENT" in codes
    assert "ROUTING_PIN_UNREACHED" in codes


def test_different_net_wire_crossing_is_rendered_short() -> None:
    circuit = circuit_with([Net(name="A", pins=[pinref("U1", "A"), pinref("U2", "A")]), Net(name="B", pins=[pinref("U3", "B"), pinref("U4", "B")])])
    codes = codes_for(
        circuit,
        [
            net_group("A"),
            net_group("B"),
            pin_element("U1", "A", 20, 100),
            pin_element("U2", "A", 180, 100),
            pin_element("U3", "B", 100, 20),
            pin_element("U4", "B", 100, 180),
            segment_element("wire-A", (20, 100, 180, 100), net="A"),
            segment_element("wire-B", (100, 20, 100, 180), net="B"),
        ],
    )
    assert "DRC_RENDERED_NET_SHORT" in codes


def test_wire_touching_wrong_net_pin_is_detected() -> None:
    circuit = circuit_with([Net(name="A", pins=[pinref("U1", "A"), pinref("U3", "A")]), Net(name="B", pins=[pinref("U2", "B")])])
    codes = codes_for(
        circuit,
        [
            net_group("A"),
            net_group("B"),
            pin_element("U1", "A", 20, 20),
            pin_element("U3", "A", 180, 20),
            pin_element("U2", "B", 120, 20),
            segment_element("wire-A", (20, 20, 120, 20), net="A"),
        ],
    )
    assert "DRC_PIN_WRONG_NET_CONTACT" in codes
    assert "ROUTING_EXTRA_PIN_ON_NET" in codes


def test_label_and_power_symbol_wrong_net_contacts_are_detected() -> None:
    circuit = circuit_with([Net(name="A", pins=[pinref("U1", "A")]), Net(name="B", pins=[pinref("U2", "B")])])
    codes = codes_for(
        circuit,
        [
            net_group("A"),
            net_group("B"),
            pin_element("U1", "A", 20, 20),
            pin_element("U2", "B", 220, 20),
            box_element("label-A", (100, 14, 150, 26), net="A", kind="net_label_endpoint"),
            box_element("symbol-A", (100, 60, 150, 100), net="A", kind="power_symbol"),
            segment_element("wire-B-1", (80, 20, 180, 20), net="B"),
            segment_element("wire-B-2", (80, 80, 180, 80), net="B"),
        ],
    )
    assert "DRC_LABEL_WRONG_NET_CONTACT" in codes
    assert "DRC_POWER_SYMBOL_WRONG_NET_CONTACT" in codes


def test_label_and_power_label_text_mismatch_are_detected() -> None:
    circuit = circuit_with([Net(name="A", pins=[pinref("U1", "A")])])
    codes = codes_for(circuit, [net_group("A", "net_label"), pin_element("U1", "A", 20, 20), text_element("label-A", "B", (60, 10, 90, 30), net="A")])
    assert "ROUTING_LABEL_NET_MISMATCH" in codes
    power_codes = codes_for(circuit, [net_group("A", "power_symbol"), pin_element("U1", "A", 20, 20), text_element("power-A", "B", (60, 10, 90, 30), net="A", kind="power_label")])
    assert "ROUTING_POWER_SYMBOL_NET_MISMATCH" in power_codes


def test_allowed_single_pin_net_without_route_passes() -> None:
    circuit = circuit_with([Net(name="TP", pins=[pinref("TP1", "TP")], allow_single=True)])
    assert codes_for(circuit, [net_group("TP"), pin_element("TP1", "TP", 20, 20)]) == []


def test_solar_555_and_multi_mosfet_clean_circuits_pass_rendered_connectivity() -> None:
    library = load_component_library(ROOT / "components")
    for path in [
        ROOT / "examples" / "solar_led.cnet",
        ROOT / "examples" / "555_timer_50_duty_astable.cnet",
        ROOT / "test_circuits" / "good" / "04_repeated_groups" / "multi_mosfet_4_channel.cnet",
    ]:
        circuit, parse_diags = NetlistParser().parse_file(path)
        assert circuit is not None, parse_diags
        layout_path = path.with_suffix(".layout.json") if path.parent.name == "examples" else path.with_name("circuit.layout.json")
        existing = Layout.model_validate_json(layout_path.read_text(encoding="utf-8")) if layout_path.exists() else None
        layout = DeterministicPlacementEngine().place(circuit, library, existing)
        routes = ManhattanRouter.for_strict().route(circuit, library, layout)
        scene = build_schematic_scene(circuit, library, layout, routes)
        diagnostics, metrics = validate_rendered_connectivity(circuit, scene)
        assert diagnostics == []
        assert metrics["net_count"] == len(circuit.nets)


def test_app_pipeline_surfaces_rendered_connectivity_diagnostics(monkeypatch) -> None:
    from circuit_netlist import app as app_module

    diagnostic = Diagnostic(
        severity=Severity.ERROR,
        code="DRC_RENDERED_NET_OPEN",
        message="fake rendered open from app integration test",
        net_name="N",
        metadata={"subsystem": "rendered_connectivity"},
    )

    def fake_validate_rendered_connectivity(circuit, scene):
        return [diagnostic], {"net_count": 1, "nets": {"N": {"logical_island_count_after_labels": 2}}}

    monkeypatch.setattr(app_module, "validate_rendered_connectivity", fake_validate_rendered_connectivity)
    context = app_module.render_pipeline(
        "CIRCUIT AppConnectivity\nCOMPONENT TP1 BASIC_TEST_POINT\nCOMPONENT TP2 BASIC_TEST_POINT\nNET N:\n TP1.TP\n TP2.TP\n",
        "app_connectivity.cnet",
        quality=RenderQualityMode.STRICT,
        use_cache=False,
    )

    assert [diag.code for diag in context["connectivity"]] == ["DRC_RENDERED_NET_OPEN"]
    assert "DRC_RENDERED_NET_OPEN" in [diag.code for diag in context["diagnostics"]]
    assert context["metrics"]["rendered_connectivity"]["net_count"] == 1
