from pathlib import Path

import pytest

from circuit_netlist.component_library import load_component_library
from circuit_netlist.hit_testing import hit_test_point
from circuit_netlist.models import Circuit, ComponentInstance, Layout, Placement
from circuit_netlist.regression import visual_drc_from_scene
from circuit_netlist.scene_builder import build_schematic_scene, has_visible_body
from circuit_netlist.scene_renderer import render_scene_svg


ROOT = Path(__file__).resolve().parents[1]


OPEN_SYMBOL_COMPONENTS = [
    "BASIC_RESISTOR",
    "BASIC_CAPACITOR_CERAMIC",
    "BASIC_CAPACITOR_POLARIZED",
    "BASIC_INDUCTOR",
    "LIGHT_LED_WHITE",
    "BASIC_NMOS",
    "BASIC_PMOS",
    "BASIC_OP_AMP",
    "BASIC_GROUND",
    "BASIC_TEST_POINT",
    "POWER_DC_SOURCE",
]

BOXED_COMPONENTS = [
    "MCU_ATtiny402_SOIC8",
    "POWER_LIPO_1S",
    "POWER_SOLAR_PANEL_GENERIC",
    "POWER_CHARGER_BQ25185_BLOCK",
    "LIGHT_LED_RGB_ADDRESSABLE",
    "BASIC_555_TIMER",
]


def library():
    return load_component_library(ROOT / "components")


def scene_for_component(component_id: str):
    lib = library()
    circuit = Circuit(
        name=f"Symbol_{component_id}",
        components=[ComponentInstance(ref="X1", component_id=component_id)],
        nets=[],
    )
    layout = Layout(components={"X1": Placement(x=120, y=100)})
    scene = build_schematic_scene(circuit, lib, layout, [])
    return lib.get(component_id), scene, render_scene_svg(scene, [])


@pytest.mark.parametrize("component_id", OPEN_SYMBOL_COMPONENTS)
def test_open_schematic_symbols_have_hidden_logical_body_without_visible_rectangle(component_id: str) -> None:
    definition, scene, svg = scene_for_component(component_id)
    assert definition is not None
    assert has_visible_body(definition) is False

    logical_body = scene.first("component-X1:body")
    assert logical_body is not None
    assert logical_body.visible is False
    assert logical_body.selectable is False
    assert logical_body.drc_enabled is True
    assert logical_body.hit_test_enabled is True
    assert logical_body.primitives[0].kind == "rect"
    assert logical_body.primitives[0].style_class == "logical-body"

    assert scene.first("component-X1:visible-body") is None
    assert 'data-scene-id="component-X1:body"' not in svg
    assert 'data-kind="component_body"' not in svg
    assert "logical-body" not in svg
    assert 'data-kind="component_visible_body"' not in svg


@pytest.mark.parametrize("component_id", BOXED_COMPONENTS)
def test_boxed_components_keep_visible_body_artwork(component_id: str) -> None:
    definition, scene, svg = scene_for_component(component_id)
    assert definition is not None
    assert has_visible_body(definition) is True

    logical_body = scene.first("component-X1:body")
    visible_body = scene.first("component-X1:visible-body")
    assert logical_body is not None
    assert visible_body is not None
    assert logical_body.visible is False
    assert visible_body.visible is True
    assert visible_body.primitives[0].kind == "rect"
    assert visible_body.metadata["body_role"] == "visible_artwork"

    assert 'data-scene-id="component-X1:body"' not in svg
    assert 'data-kind="component_body"' not in svg
    assert 'data-scene-id="component-X1:visible-body"' in svg
    assert 'data-kind="component_visible_body"' in svg


def test_hidden_logical_bounds_still_drive_component_overlap_drc() -> None:
    lib = library()
    circuit = Circuit(
        name="Hidden_Body_DRC",
        components=[
            ComponentInstance(ref="R1", component_id="BASIC_RESISTOR"),
            ComponentInstance(ref="R2", component_id="BASIC_RESISTOR"),
        ],
        nets=[],
    )
    layout = Layout(components={"R1": Placement(x=100, y=100), "R2": Placement(x=100, y=100)})
    scene = build_schematic_scene(circuit, lib, layout, [])

    assert all(element.visible is False for element in scene.elements_by_kind("component_body"))
    assert not scene.elements_by_kind("component_visible_body")
    diagnostics, metrics = visual_drc_from_scene(scene)
    assert metrics["component_body_overlaps"] == 1
    assert "DRC_COMPONENT_OVERLAP" in [diag.code for diag in diagnostics]


def test_hidden_logical_bounds_still_allow_open_symbol_component_selection() -> None:
    _, scene, svg = scene_for_component("BASIC_RESISTOR")
    body = scene.first("component-X1:body")
    assert body is not None and body.visible is False

    hit = hit_test_point(scene, (body.bounds.min_x + body.bounds.max_x) / 2, (body.bounds.min_y + body.bounds.max_y) / 2)
    assert hit is not None
    assert hit.kind == "component_group"
    assert hit.component_ref == "X1"
    assert '<rect class="hit-area"' in svg


def test_visible_body_policy_accepts_explicit_metadata_override() -> None:
    lib = library()
    resistor = lib.get("BASIC_RESISTOR")
    charger = lib.get("POWER_CHARGER_BQ25185_BLOCK")
    assert resistor is not None and charger is not None

    assert has_visible_body(resistor.model_copy(update={"metadata": {"visual_body": "block"}})) is True
    assert has_visible_body(charger.model_copy(update={"metadata": {"visual_body": "symbol_only"}})) is False
