from pathlib import Path

from circuit_netlist.component_library import load_component_library
from circuit_netlist.parser import NetlistParser
from circuit_netlist.placement import DeterministicPlacementEngine
from circuit_netlist.regression import visual_drc, visual_drc_from_scene
from circuit_netlist.router import ManhattanRouter
from circuit_netlist.scene import Bounds, Point, RenderPrimitive, SceneElement, SchematicScene, TextGeometry


ROOT = Path(__file__).resolve().parents[1]


def scene_with(elements: list[SceneElement]) -> SchematicScene:
    return SchematicScene(circuit_name="VisibleDRC", canvas_bounds=Bounds(min_x=0, min_y=0, max_x=500, max_y=500), elements=elements)


def segment_element(
    element_id: str,
    segment: tuple[int, int, int, int],
    *,
    net: str = "A",
    kind: str = "wire",
    parent: str = "net-A",
    wire_kind: str = "wire",
    source_ref: str = "",
) -> SceneElement:
    x1, y1, x2, y2 = segment
    bounds = Bounds(min_x=min(x1, x2), min_y=min(y1, y2), max_x=max(x1, x2), max_y=max(y1, y2))
    return SceneElement(
        id=element_id,
        kind=kind,
        layer="wires",
        parent_id=parent,
        net_name=net,
        bounds=bounds,
        primitives=[RenderPrimitive(kind="line", geometry={"x1": x1, "y1": y1, "x2": x2, "y2": y2})],
        metadata={"wire_kind": wire_kind, "source_ref": source_ref, "segment": segment},
    )


def text_element(element_id: str, box: tuple[float, float, float, float], *, kind: str = "net_label", net: str = "B", parent: str = "net-B") -> SceneElement:
    bounds = Bounds.from_tuple(box)
    return SceneElement(
        id=element_id,
        kind=kind,
        layer="wires",
        parent_id=parent,
        net_name=net,
        bounds=bounds,
        collision_bounds=bounds,
        text=TextGeometry(text=net, origin=Point(x=box[0], y=box[3]), anchor="start", bounds=bounds),
        primitives=[RenderPrimitive(kind="text", geometry={"x": box[0], "y": box[3], "text": net})],
        metadata={"drc_wire_text": True, "text_role": kind},
    )


def box_element(element_id: str, box: tuple[float, float, float, float], *, kind: str, net: str = "B", parent: str = "net-B", ref: str | None = None) -> SceneElement:
    bounds = Bounds.from_tuple(box)
    return SceneElement(
        id=element_id,
        kind=kind,
        layer="wires" if "symbol" not in kind or kind in {"power_symbol", "ground_symbol"} else "geometry",
        parent_id=parent,
        net_name=net,
        component_ref=ref,
        bounds=bounds,
        collision_bounds=bounds,
        primitives=[RenderPrimitive(kind="rect", geometry={"x": box[0], "y": box[1], "width": box[2] - box[0], "height": box[3] - box[1]})],
    )


def pin_element(ref: str, x: int, y: int) -> SceneElement:
    bounds = Bounds(min_x=x - 4, min_y=y - 4, max_x=x + 4, max_y=y + 4)
    return SceneElement(
        id=f"pin-{ref}-1",
        kind="pin",
        layer="geometry",
        component_ref=ref,
        bounds=bounds,
        collision_bounds=bounds,
        primitives=[RenderPrimitive(kind="circle", geometry={"cx": x, "cy": y, "r": 4})],
    )


def codes_for(elements: list[SceneElement]) -> list[str]:
    diagnostics, _ = visual_drc_from_scene(scene_with(elements))
    return [diag.code for diag in diagnostics]


def test_wire_crosses_net_label_text_is_reported() -> None:
    codes = codes_for([segment_element("wire-A", (40, 100, 220, 100)), text_element("label-B", (100, 90, 160, 110))])
    assert "DRC_WIRE_TEXT_OVERLAP" in codes


def test_wire_crosses_power_label_text_is_reported() -> None:
    codes = codes_for([segment_element("wire-A", (40, 100, 220, 100)), text_element("power-B", (100, 90, 160, 110), kind="power_label")])
    assert "DRC_WIRE_TEXT_OVERLAP" in codes


def test_wire_crosses_label_endpoint_shape_is_reported() -> None:
    codes = codes_for([segment_element("wire-A", (40, 100, 220, 100)), box_element("flag-B", (100, 92, 160, 108), kind="net_label_endpoint")])
    assert "DRC_WIRE_LABEL_OVERLAP" in codes


def test_label_stub_crosses_another_label_is_reported() -> None:
    codes = codes_for(
        [
            segment_element("stub-A", (120, 40, 120, 180), kind="wire_stub", wire_kind="label-stub"),
            text_element("label-B", (100, 90, 160, 110)),
        ]
    )
    assert "DRC_STUB_LABEL_OVERLAP" in codes


def test_power_symbol_stub_crosses_component_symbol_is_reported() -> None:
    codes = codes_for(
        [
            segment_element("stub-A", (40, 100, 220, 100), kind="wire_stub", wire_kind="power-stub", source_ref="R1"),
            box_element("symbol-U1", (100, 80, 160, 140), kind="component_symbol", ref="U1"),
        ]
    )
    assert "DRC_WIRE_SYMBOL_OVERLAP" in codes
    assert "DRC_STUB_SYMBOL_OVERLAP" in codes


def test_same_net_accidental_visual_overlap_is_reported_when_not_own_attachment() -> None:
    codes = codes_for(
        [
            segment_element("stub-A", (120, 40, 120, 180), kind="wire_stub", net="SAME", parent="net-SAME:a", wire_kind="label-stub"),
            text_element("label-SAME", (100, 90, 160, 110), net="SAME", parent="net-SAME:b"),
        ]
    )
    assert "DRC_STUB_LABEL_OVERLAP" in codes


def test_intentional_label_stub_contact_is_allowed() -> None:
    codes = codes_for(
        [
            segment_element("stub-A", (40, 100, 100, 100), kind="wire_stub", parent="net-A", wire_kind="label-stub"),
            box_element("flag-A", (100, 92, 160, 108), kind="net_label_endpoint", net="A", parent="net-A"),
        ]
    )
    assert "DRC_STUB_LABEL_OVERLAP" not in codes


def test_intentional_power_symbol_contact_is_allowed() -> None:
    codes = codes_for(
        [
            segment_element("stub-A", (40, 100, 100, 100), kind="wire_stub", parent="net-A", wire_kind="power-stub"),
            box_element("power-A", (100, 80, 160, 120), kind="power_symbol", net="A", parent="net-A"),
        ]
    )
    assert "DRC_POWER_SYMBOL_OVERLAP" not in codes


def test_intentional_pin_contact_is_allowed() -> None:
    codes = codes_for(
        [
            segment_element("wire-A", (100, 100, 220, 100), source_ref="R1"),
            pin_element("U1", 100, 100),
            box_element("symbol-U1", (80, 80, 160, 140), kind="component_symbol", ref="U1"),
        ]
    )
    assert "DRC_WIRE_SYMBOL_OVERLAP" not in codes


def test_solar_circuit_has_zero_visible_overlap_diagnostics() -> None:
    library = load_component_library(ROOT / "components")
    circuit, diagnostics = NetlistParser().parse_file(ROOT / "examples" / "solar_led.cnet")
    assert circuit is not None, diagnostics
    layout = DeterministicPlacementEngine().place(circuit, library)
    routes = ManhattanRouter(validate_auto_route_styles=False).route(circuit, library, layout)
    drc, metrics = visual_drc(circuit, library, layout, routes)
    assert [diag.code for diag in drc] == []
    assert metrics["wire_label_overlaps"] == 0
    assert metrics["stub_label_overlaps"] == 0
    assert metrics["power_symbol_overlaps"] == 0
