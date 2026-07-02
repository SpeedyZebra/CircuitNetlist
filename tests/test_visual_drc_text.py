from pathlib import Path

from circuit_netlist.component_library import load_component_library
from circuit_netlist.models import Circuit, ComponentInstance, Layout, Net, PinRef, Placement, RoutedNet
from circuit_netlist.regression import visual_drc


ROOT = Path(__file__).resolve().parents[1]


def basic_circuit() -> Circuit:
    return Circuit(
        name="TextDRC",
        components=[
            ComponentInstance(ref="R1", component_id="BASIC_RESISTOR", parameters={"value": "1kohm"}),
            ComponentInstance(ref="R2", component_id="BASIC_RESISTOR", parameters={"value": "2kohm"}),
        ],
        nets=[Net(name="N", pins=[PinRef(component_ref="R1", pin_name="1"), PinRef(component_ref="R2", pin_name="1")])],
    )


def basic_layout() -> Layout:
    return Layout(components={"R1": Placement(x=40, y=200), "R2": Placement(x=240, y=200)})


def drc_codes(routes: list[RoutedNet]) -> list[str]:
    library = load_component_library(ROOT / "components")
    diagnostics, _ = visual_drc(basic_circuit(), library, basic_layout(), routes)
    return [diag.code for diag in diagnostics]


def test_physical_wire_crossing_unrelated_reference_text_warns() -> None:
    routes = [RoutedNet(name="X", segments=[(120, 190, 340, 190)])]
    assert "DRC_WIRE_TEXT_OVERLAP" in drc_codes(routes)


def test_physical_wire_crossing_unrelated_value_text_warns() -> None:
    routes = [RoutedNet(name="X", segments=[(120, 298, 340, 298)])]
    assert "DRC_WIRE_TEXT_OVERLAP" in drc_codes(routes)


def test_wire_touching_own_connected_component_text_is_ignored() -> None:
    routes = [RoutedNet(name="X", segments=[(240, 240, 240, 298)])]
    assert "DRC_WIRE_TEXT_OVERLAP" not in drc_codes(routes)


def test_net_label_stub_text_contact_is_not_physical_wire_text_drc() -> None:
    routes = [
        RoutedNet(
            name="LABEL_NET",
            render_style="net_label",
            endpoints=[{"component_ref": "R1", "pin_name": "1", "pin_number": "1", "side": "left", "x": 40, "y": 240}],
        )
    ]
    assert "DRC_WIRE_TEXT_OVERLAP" not in drc_codes(routes)


def test_unrelated_wire_text_collision_is_stable() -> None:
    routes = [
        RoutedNet(name="A", segments=[(120, 190, 340, 190)]),
        RoutedNet(name="B", segments=[(120, 320, 340, 320)]),
    ]
    codes = drc_codes(routes)
    assert codes.count("DRC_WIRE_TEXT_OVERLAP") == 1
