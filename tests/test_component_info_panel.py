from pathlib import Path

from fastapi import HTTPException
import pytest

from circuit_netlist import app as app_module
from circuit_netlist.app import LoadCaseRequest, component_detail_payload
from circuit_netlist.component_library import load_component_library
from circuit_netlist.models import Circuit, ComponentInstance, Layout, Placement


ROOT = Path(__file__).resolve().parents[1]


def library():
    return load_component_library(ROOT / "components")


def metadata(component_id: str) -> dict:
    component = library().get(component_id)
    assert component is not None
    return component.metadata


@pytest.mark.parametrize(
    "component_id, expected",
    [
        ("BASIC_555_TIMER", "timer"),
        ("MCU_ATtiny402_SOIC8", "microcontroller"),
        ("BASIC_RESISTOR", "current"),
        ("BASIC_NMOS", "transistor"),
        ("BASIC_OP_AMP", "operational amplifier"),
        ("POWER_CHARGER_BQ25185_BLOCK", "charger"),
        ("POWER_SOLAR_PANEL_GENERIC", "photovoltaic"),
        ("POWER_LIPO_1S", "lipo"),
        ("LIGHT_LED_WHITE", "light-emitting"),
        ("BASIC_CAPACITOR_CERAMIC", "capacitor"),
    ],
)
def test_common_components_have_brief_metadata(component_id: str, expected: str) -> None:
    info = metadata(component_id)
    brief = " ".join(str(info.get(key, "")) for key in ["summary", "function", "common_use"])
    assert expected.lower() in brief.lower()
    assert info.get("verified_status")


def test_555_timer_pin_descriptions_are_available_from_library_and_api() -> None:
    info = metadata("BASIC_555_TIMER")
    for pin in ["VCC", "GND", "TRIG", "THRESH", "DISCH", "OUT", "RESET", "CTRL"]:
        assert info["pins"][pin]["description"]

    app_module.load_case(LoadCaseRequest(case_id="example_555_timer_50_duty_astable"))
    detail = app_module.current_component_details("U1")
    assert detail["name"] == "555 Timer"
    assert "timer" in detail["summary"].lower()
    pin_descriptions = {pin["name"]: pin["description"] for pin in detail["pins"]}
    assert "starts" in pin_descriptions["TRIG"].lower()
    assert "positive" in pin_descriptions["VCC"].lower()


def test_component_details_include_identity_value_orientation_connected_nets() -> None:
    app_module.load_case(LoadCaseRequest(case_id="example_solar_led"))
    detail = app_module.current_component_details("R_LED")
    assert detail["ref"] == "R_LED"
    assert detail["component_id"] == "BASIC_RESISTOR"
    assert detail["category"] == "Basic"
    assert detail["value"] == "68ohm"
    assert detail["orientation"] in {0, 90, 180, 270}
    connected = {pin["name"]: pin["connected_net"] for pin in detail["pins"]}
    assert connected == {"1": "VBAT", "2": "LED_ANODE"}


def test_component_details_include_detected_topology_patterns() -> None:
    app_module.load_case(LoadCaseRequest(case_id="example_solar_led"))
    detail = app_module.current_component_details("R_LED")
    assert detail["topology_patterns"]
    assert any(pattern["type"] == "led_current_limit" for pattern in detail["topology_patterns"])


def test_unknown_component_detail_falls_back_to_generic_info() -> None:
    circuit = Circuit(name="UnknownPart", components=[ComponentInstance(ref="X1", component_id="MISSING_PART")], nets=[])
    context = {"circuit": circuit, "library": library(), "layout": Layout(components={"X1": Placement(x=0, y=0)}), "analysis": None}
    detail = component_detail_payload("X1", context)
    assert detail["component_id"] == "MISSING_PART"
    assert detail["summary"] == "Unknown component definition."
    assert detail["pins"] == []


def test_missing_component_detail_raises_404() -> None:
    circuit = Circuit(name="Empty", components=[], nets=[])
    context = {"circuit": circuit, "library": library(), "layout": Layout(), "analysis": None}
    with pytest.raises(HTTPException):
        component_detail_payload("NOPE", context)


def test_frontend_source_renders_component_brief_and_pin_table() -> None:
    source = (ROOT / "src" / "circuit_netlist" / "static" / "app.js").read_text(encoding="utf-8")
    assert "renderComponentDetails" in source
    assert "pinTable" in source
    assert "/api/circuit/component-details/" in source
    assert "Topology patterns" in source
    assert "verified_status" in source
