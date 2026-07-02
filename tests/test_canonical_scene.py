from pathlib import Path

from circuit_netlist import app as app_module
from circuit_netlist.component_library import load_component_library
from circuit_netlist.exporters import export_png_placeholder_from_scene
from circuit_netlist.hit_testing import hit_test_point
from circuit_netlist.parser import NetlistParser
from circuit_netlist.placement import DeterministicPlacementEngine
from circuit_netlist.regression import visual_drc, visual_drc_from_scene, wire_like_segments_from_scene
from circuit_netlist.renderer import render_circuit
from circuit_netlist.router import ManhattanRouter
from circuit_netlist.scene import Bounds, Point, SchematicScene
from circuit_netlist.scene_builder import build_schematic_scene
from circuit_netlist.scene_renderer import render_scene_svg


ROOT = Path(__file__).resolve().parents[1]


def load_scene():
    library = load_component_library(ROOT / "components")
    circuit, diagnostics = NetlistParser().parse_file(ROOT / "examples" / "solar_led.cnet")
    assert circuit is not None, diagnostics
    layout = DeterministicPlacementEngine().place(circuit, library)
    routes = ManhattanRouter().route(circuit, library, layout)
    scene = build_schematic_scene(circuit, library, layout, routes)
    return library, circuit, layout, routes, scene


def test_scene_primitives_are_typed_and_queryable() -> None:
    bounds = Bounds(min_x=0, min_y=0, max_x=10, max_y=20)
    assert bounds.width == 10
    assert bounds.height == 20
    assert bounds.expanded(2).contains_point(Point(x=-1, y=10))
    assert bounds.intersects(Bounds(min_x=9, min_y=19, max_x=12, max_y=24))


def test_scene_builder_contains_canonical_render_and_geometry_elements() -> None:
    _, _, _, _, scene = load_scene()
    assert scene.scene_version == "1.0"
    assert scene.elements_by_kind("component_group")
    assert scene.elements_by_kind("component_body")
    assert scene.elements_by_kind("component_symbol")
    assert scene.elements_by_kind("pin")
    assert scene.elements_by_kind("wire")
    assert scene.first("component-CHG1:body") is not None
    assert all(element.metadata.get("orientation") != "diagonal" for element in scene.elements_by_kind("wire"))


def test_render_circuit_uses_scene_svg_with_stable_existing_markup() -> None:
    library, circuit, layout, routes, scene = load_scene()
    assert render_circuit(circuit, library, layout, routes, []) == render_scene_svg(scene, [])
    svg = render_scene_svg(scene, [])
    assert 'data-scene-version="1.0"' in svg
    assert 'id="component-CHG1"' in svg
    assert 'id="net-VBAT"' in svg


def test_visual_drc_consumes_scene_geometry() -> None:
    library, circuit, layout, routes, scene = load_scene()
    diagnostics, metrics = visual_drc(circuit, library, layout, routes)
    scene_diagnostics, scene_metrics = visual_drc_from_scene(scene)
    assert [diag.code for diag in diagnostics] == [diag.code for diag in scene_diagnostics]
    assert metrics["total_wire_length"] == scene_metrics["total_wire_length"]
    assert wire_like_segments_from_scene(scene)


def test_hit_testing_uses_scene_hit_bounds() -> None:
    _, _, _, _, scene = load_scene()
    body = scene.first("component-CHG1:body")
    assert body is not None
    hit = hit_test_point(scene, (body.bounds.min_x + body.bounds.max_x) / 2, (body.bounds.min_y + body.bounds.max_y) / 2)
    assert hit is not None
    assert hit.component_ref == "CHG1"


def test_scene_endpoint_and_scene_export_helpers(tmp_path: Path) -> None:
    payload = app_module.current_scene_payload()
    assert payload["scene"]["scene_version"] == "1.0"
    scene = SchematicScene.model_validate(payload["scene"])
    png_path = export_png_placeholder_from_scene(scene, tmp_path / "scene.png")
    assert "Scene version: 1.0" in png_path.read_text(encoding="utf-8")
