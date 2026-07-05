from pathlib import Path
import re

import pytest

from circuit_netlist import app as app_module
from circuit_netlist.component_library import load_component_library
from circuit_netlist.exporters import export_png_from_scene
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
pytestmark = [pytest.mark.integration, pytest.mark.slow]


def load_scene():
    library = load_component_library(ROOT / "components")
    circuit, diagnostics = NetlistParser().parse_file(ROOT / "examples" / "solar_led.cnet")
    assert circuit is not None, diagnostics
    layout = DeterministicPlacementEngine().place(circuit, library)
    routes = ManhattanRouter(validate_auto_route_styles=False).route(circuit, library, layout)
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
    scene_ids = re.findall(r'data-scene-id="([^"]+)"', svg)
    assert scene_ids
    assert len(scene_ids) == len(set(scene_ids))
    assert not any("svg" in element.metadata for element in scene.elements)


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
    png_path = export_png_from_scene(scene, tmp_path / "scene.png")
    data = png_path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    from PIL import Image

    with Image.open(png_path) as image:
        assert image.size == (int(scene.canvas_bounds.width), int(scene.canvas_bounds.height))


def test_hidden_body_primitive_mutation_controls_drc_without_changing_svg() -> None:
    _, _, _, _, scene = load_scene()
    first = scene.first("component-CHG1:body")
    second = scene.first("component-BAT1:body")
    assert first is not None and second is not None
    assert first.visible is False
    svg_before = render_scene_svg(scene, [])
    first.primitives[0].geometry.update(second.primitives[0].geometry)
    diagnostics, _ = visual_drc_from_scene(scene)
    svg_after = render_scene_svg(scene, [])
    assert svg_before == svg_after
    assert "DRC_COMPONENT_OVERLAP" in [diag.code for diag in diagnostics]


def test_visible_body_primitive_mutation_controls_svg() -> None:
    _, _, _, _, scene = load_scene()
    first = scene.first("component-CHG1:visible-body")
    second = scene.first("component-BAT1:visible-body")
    assert first is not None and second is not None
    svg_before = render_scene_svg(scene, [])
    first.primitives[0].geometry.update(second.primitives[0].geometry)
    svg_after = render_scene_svg(scene, [])
    assert svg_before != svg_after


def test_wire_primitive_mutation_controls_svg_drc_and_hit_testing() -> None:
    _, _, _, _, scene = load_scene()
    wire = scene.elements_by_kind("wire")[0]
    svg_before = render_scene_svg(scene, [])
    wire.primitives[0].geometry.update({"x1": 25, "y1": 25, "x2": 225, "y2": 25})
    svg_after = render_scene_svg(scene, [])
    assert svg_before != svg_after
    assert hit_test_point(scene, 125, 25, tolerance=3).id == wire.id
    _, metrics = visual_drc_from_scene(scene)
    assert metrics["total_wire_length"] >= 200


def test_text_primitive_mutation_and_element_removal_control_svg() -> None:
    _, _, _, _, scene = load_scene()
    text_element = scene.first("component-CHG1:ref-label")
    assert text_element is not None and text_element.text is not None
    svg_before = render_scene_svg(scene, [])
    text_element.text.origin.x += 40
    text_element.primitives[0].geometry["x"] = text_element.text.origin.x
    assert render_scene_svg(scene, []) != svg_before

    wire = scene.elements_by_kind("wire")[0]
    scene.elements.remove(wire)
    svg_removed = render_scene_svg(scene, [])
    assert wire.id not in svg_removed
    assert all(item[3] != tuple(wire.primitives[0].geometry[key] for key in ["x1", "y1", "x2", "y2"]) for item in wire_like_segments_from_scene(scene))


def test_scene_hit_test_api_returns_ordered_scene_ids() -> None:
    app_module.load_case(app_module.LoadCaseRequest(case_id="example_solar_led"))
    payload_scene = app_module.current_scene_payload()["scene"]
    scene = SchematicScene.model_validate(payload_scene)
    body = scene.first("component-CHG1:body")
    assert body is not None
    payload = app_module.current_scene_hit_test(app_module.HitTestRequest(x=(body.bounds.min_x + body.bounds.max_x) / 2, y=(body.bounds.min_y + body.bounds.max_y) / 2))
    assert payload["hits"]
    assert {"scene_id", "kind", "priority"} <= set(payload["hits"][0])


def test_scene_builder_does_not_depend_on_legacy_renderer() -> None:
    source = (ROOT / "src" / "circuit_netlist" / "scene_builder.py").read_text(encoding="utf-8")
    assert "from .renderer" not in source
    assert "render_component(" not in source
    assert '"svg"' not in source
