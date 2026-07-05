from pathlib import Path

import pytest

from circuit_netlist import app as app_module
from circuit_netlist.app import LoadCaseRequest, NetRouteStyleRequest, apply_net_route_style
from circuit_netlist.component_library import load_component_library
from circuit_netlist.models import Circuit, ComponentInstance, Layout, Net, NetRouteStyle, PinRef, Placement
from circuit_netlist.parser import NetlistParser
from circuit_netlist.placement import DeterministicPlacementEngine
from circuit_netlist.router import ManhattanRouter


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.slow


def load_solar():
    library = load_component_library(ROOT / "components")
    circuit, diagnostics = NetlistParser().parse_file(ROOT / "examples" / "solar_led.cnet")
    assert circuit is not None, diagnostics
    layout = DeterministicPlacementEngine().place(circuit, library)
    return circuit, library, layout


def route_by_name(circuit, library, layout):
    return {route.name: route for route in ManhattanRouter().route(circuit, library, layout)}


def close_two_pin_circuit() -> tuple[Circuit, Layout]:
    circuit = Circuit(
        name="CloseNet",
        components=[
            ComponentInstance(ref="R1", component_id="BASIC_RESISTOR"),
            ComponentInstance(ref="R2", component_id="BASIC_RESISTOR"),
        ],
        nets=[Net(name="LOCAL", pins=[PinRef(component_ref="R1", pin_name="2"), PinRef(component_ref="R2", pin_name="1")])],
    )
    layout = Layout(components={"R1": Placement(x=100, y=100), "R2": Placement(x=300, y=100)})
    return circuit, layout


def obstructed_two_pin_circuit() -> tuple[Circuit, Layout]:
    circuit = Circuit(
        name="ObstructedNet",
        components=[
            ComponentInstance(ref="R1", component_id="BASIC_RESISTOR"),
            ComponentInstance(ref="R2", component_id="BASIC_RESISTOR"),
            ComponentInstance(ref="U1", component_id="BASIC_555_TIMER"),
        ],
        nets=[Net(name="CROSS_BLOCK", pins=[PinRef(component_ref="R1", pin_name="2"), PinRef(component_ref="R2", pin_name="1")])],
    )
    layout = Layout(
        components={
            "R1": Placement(x=40, y=200),
            "R2": Placement(x=520, y=200),
            "U1": Placement(x=250, y=120),
        }
    )
    return circuit, layout


def test_manual_route_style_overrides_render_style() -> None:
    circuit, library, layout = load_solar()

    apply_net_route_style(layout, "LED_ANODE", NetRouteStyle.LABEL)
    routes = route_by_name(circuit, library, layout)
    assert routes["LED_ANODE"].render_style == "net_label"
    assert routes["LED_ANODE"].metadata["route_style"]["manual_override"] == "label"

    apply_net_route_style(layout, "LED_ANODE", NetRouteStyle.DIRECT)
    routes = route_by_name(circuit, library, layout)
    assert routes["LED_ANODE"].render_style == "local_wire"

    apply_net_route_style(layout, "LED_ANODE", NetRouteStyle.POWER_SYMBOL)
    routes = route_by_name(circuit, library, layout)
    assert routes["LED_ANODE"].render_style == "power_symbol"


def test_clearing_route_style_returns_to_auto_and_layout_json_round_trips() -> None:
    layout = Layout()
    apply_net_route_style(layout, "LED_ANODE", NetRouteStyle.LABEL)
    assert layout.nets["LED_ANODE"]["route_style"] == "label"

    reloaded = Layout.model_validate_json(layout.model_dump_json())
    assert reloaded.nets["LED_ANODE"]["route_style"] == "label"

    apply_net_route_style(reloaded, "LED_ANODE", NetRouteStyle.AUTO)
    assert "LED_ANODE" not in reloaded.nets


def test_auto_route_style_chooses_direct_for_close_simple_net() -> None:
    library = load_component_library(ROOT / "components")
    circuit, layout = close_two_pin_circuit()
    route = route_by_name(circuit, library, layout)["LOCAL"]
    assert route.render_style == "local_wire"
    assert route.metadata["route_style"]["selected_style"] == "direct"
    assert route.metadata["route_style"]["direct_drc_status"] == "scene_clean"
    assert route.metadata["route_style"]["candidate_validation"] == "scene_drc"
    assert "direct" in route.metadata["route_style"]["candidates_considered"]


def test_auto_route_style_chooses_label_for_obstructed_direct_candidate() -> None:
    library = load_component_library(ROOT / "components")
    circuit, layout = obstructed_two_pin_circuit()
    route = route_by_name(circuit, library, layout)["CROSS_BLOCK"]
    assert route.render_style == "net_label"
    assert route.metadata["route_style"]["selected_style"] == "label"
    assert route.metadata["route_style"]["direct_drc_status"] == "scene_drc_failed"
    assert route.metadata["route_style"]["label_drc_status"] == "scene_clean"


def test_auto_route_style_chooses_power_symbol_for_global_rails() -> None:
    circuit, library, layout = load_solar()
    routes = route_by_name(circuit, library, layout)
    assert routes["VBAT"].render_style == "power_symbol"
    assert routes["GND"].render_style == "power_symbol"
    assert routes["VBAT"].metadata["route_style"]["candidate_validation"] == "scene_drc"
    assert routes["GND"].metadata["route_style"]["candidate_validation"] == "scene_drc"


def test_route_style_candidate_debug_metadata_is_exposed() -> None:
    library = load_component_library(ROOT / "components")
    circuit, layout = obstructed_two_pin_circuit()
    route = route_by_name(circuit, library, layout)["CROSS_BLOCK"]
    metadata = route.metadata["route_style"]
    assert metadata["candidate_validation"] == "scene_drc"
    assert metadata["candidates_considered"] == ["direct", "label"]
    assert set(metadata["diagnostics_by_candidate"]) == {"direct", "label"}
    assert metadata["diagnostics_by_candidate"]["direct"]["new_visual_diagnostic_codes"]
    assert metadata["diagnostics_by_candidate"]["label"]["new_visual_diagnostic_codes"] == []
    assert metadata["actual_or_estimated_lengths"]["direct"] >= 0
    assert metadata["bend_counts"]["label"] == 0


def test_route_style_api_preserves_connectivity_and_exposes_net_details() -> None:
    loaded = app_module.load_case(LoadCaseRequest(case_id="example_solar_led"))
    before = loaded["schematic"]["circuit"]["nets"]
    layout = Layout.model_validate(loaded["schematic"]["layout"])

    routed = app_module.set_net_route_style(NetRouteStyleRequest(net_name="LED_ANODE", route_style=NetRouteStyle.LABEL, layout=layout))
    after = routed["schematic"]["circuit"]["nets"]
    assert before == after

    detail = app_module.current_net_details("LED_ANODE")
    assert detail["available_route_styles"] == ["auto", "direct", "label", "power_symbol"]
    assert detail["manual_override"] == "label"
    assert detail["resolved_route_style"] == "label"


def test_frontend_source_exposes_route_style_control_for_wires_and_labels() -> None:
    source = (ROOT / "src" / "circuit_netlist" / "static" / "app.js").read_text(encoding="utf-8")
    assert "renderNetDetails" in source
    assert "route-style-select" in source
    assert "/api/circuit/net-details/" in source
    assert "/api/layout/net-route-style" in source
    assert '["direct", "Direct wire"]' in source
    assert '["label", "Net labels"]' in source
    assert '["power_symbol", "Power symbols"]' in source


def test_router_policy_does_not_hardcode_example_specific_solar_net_names() -> None:
    source = (ROOT / "src" / "circuit_netlist" / "router.py").read_text(encoding="utf-8")
    for forbidden in ("SUN_SENSE", "BAT_SENSE", "LED_ENABLE", "LED_ANODE", "LED_SWITCH", "PANEL_POS", "LABEL_PREFERRED_NETS"):
        assert forbidden not in source


def test_solar_route_styles_are_explained_without_exact_net_name_policy() -> None:
    circuit, library, layout = load_solar()
    routes = route_by_name(circuit, library, layout)
    assert routes["GND"].metadata["route_style"]["reason"] == "scene DRC candidate validation selected cleanest readable route"
    assert routes["VBAT"].metadata["route_style"]["reason"] == "scene DRC candidate validation selected cleanest readable route"
    assert routes["LED_ENABLE"].metadata["route_style"]["reason"] in {
        "generic control or sense net prefers labels",
        "scene DRC candidate validation selected cleanest readable route",
    }
