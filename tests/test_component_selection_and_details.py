from __future__ import annotations

from pathlib import Path

import pytest

from circuit_netlist import app as app_module
from circuit_netlist.app import LoadCaseRequest, component_detail_payload
from circuit_netlist.component_library import load_component_library
from circuit_netlist.models import Circuit, ComponentInstance, Layout, Placement
from circuit_netlist.scene_builder import build_schematic_scene
from circuit_netlist.scene_renderer import render_scene_svg


ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "src" / "circuit_netlist" / "static" / "app.js"


def library():
    return load_component_library(ROOT / "components")


def component_svg(component_id: str) -> str:
    circuit = Circuit(name=f"Selection_{component_id}", components=[ComponentInstance(ref="X1", component_id=component_id)], nets=[])
    layout = Layout(components={"X1": Placement(x=120, y=100)})
    scene = build_schematic_scene(circuit, library(), layout, [])
    return render_scene_svg(scene, [])


@pytest.mark.parametrize(
    "component_id",
    [
        "BASIC_RESISTOR",
        "BASIC_CAPACITOR_CERAMIC",
        "BASIC_NMOS",
        "BASIC_555_TIMER",
        "MCU_ATtiny402_SOIC8",
        "POWER_CHARGER_BQ25185_BLOCK",
        "POWER_SOLAR_PANEL_GENERIC",
        "POWER_LIPO_1S",
    ],
)
def test_component_group_svg_contains_selection_metadata(component_id: str) -> None:
    svg = component_svg(component_id)
    assert '<g id="component-X1"' in svg
    assert 'data-scene-id="component-X1"' in svg
    assert 'data-kind="component_group"' in svg
    assert 'data-selectable="true"' in svg
    assert 'data-ref="X1"' in svg
    assert 'data-component-ref="X1"' in svg
    assert f'data-component-id="{component_id}"' in svg


def test_component_hit_area_is_not_dead_selection_target() -> None:
    svg = component_svg("BASIC_RESISTOR")
    hit_start = svg.index('<rect class="hit-area"')
    hit_end = svg.index("/>", hit_start)
    hit_area = svg[hit_start:hit_end]
    assert 'data-owner-id="component-X1"' in hit_area
    assert 'data-kind="component_group"' in hit_area
    assert 'data-selectable="true"' in hit_area
    assert 'data-ref="X1"' in hit_area
    assert 'data-component-ref="X1"' in hit_area
    assert 'data-component-id="BASIC_RESISTOR"' in hit_area


def test_frontend_component_selection_source_paths_are_present() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    assert 'svg.addEventListener("click", onSvgClick)' in source
    assert "semanticSelectionTarget" in source
    assert "selectComponentByRef" in source
    assert "selectPinElement" in source
    assert "selectNetElement" in source
    assert "/api/circuit/component-details/" in source
    assert "/api/circuit/net-details/" in source
    assert "route-style-select" in source
    assert "drag.type === \"component\" && !drag.active && !drag.moved" in source


def test_component_details_api_returns_555_and_attiny_pin_descriptions() -> None:
    app_module.load_case(LoadCaseRequest(case_id="example_555_timer_50_duty_astable"))
    timer = app_module.current_component_details("U1")
    assert timer["component_id"] == "BASIC_555_TIMER"
    assert "timer" in timer["summary"].lower()
    assert any(pin["name"] == "TRIG" and pin["description"] for pin in timer["pins"])

    app_module.load_case(LoadCaseRequest(case_id="example_solar_led"))
    mcu = app_module.current_component_details("U1")
    assert mcu["component_id"] == "MCU_ATtiny402_SOIC8"
    assert "microcontroller" in mcu["summary"].lower()
    assert any(pin["name"] in {"VDD", "GND"} and pin["description"] for pin in mcu["pins"])


def test_component_details_api_returns_basic_resistor_and_capacitor_info() -> None:
    circuit = Circuit(
        name="PassiveDetails",
        components=[
            ComponentInstance(ref="R1", component_id="BASIC_RESISTOR"),
            ComponentInstance(ref="C1", component_id="BASIC_CAPACITOR_CERAMIC"),
        ],
        nets=[],
    )
    context = {"circuit": circuit, "library": library(), "layout": Layout(components={"R1": Placement(x=0, y=0), "C1": Placement(x=200, y=0)}), "analysis": None}
    resistor = component_detail_payload("R1", context)
    capacitor = component_detail_payload("C1", context)
    assert resistor["component_id"] == "BASIC_RESISTOR"
    assert "resistor" in resistor["name"].lower()
    assert "current" in resistor["summary"].lower()
    assert {pin["number"] for pin in resistor["pins"]} == {"1", "2"}
    assert capacitor["component_id"] == "BASIC_CAPACITOR_CERAMIC"
    assert "capacitor" in capacitor["name"].lower()
    assert "capacitor" in capacitor["summary"].lower()
    assert {pin["number"] for pin in capacitor["pins"]} == {"1", "2"}


def test_component_details_panel_has_no_duplicate_orientation_row() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    assert source.count("Orientation: detail.orientation") == 1
    assert "Human name" in source
    assert "Common use" in source
