from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "src" / "circuit_netlist" / "static" / "app.js"


def app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def test_component_drag_uses_threshold_and_click_suppression() -> None:
    source = app_js()
    assert "const DRAG_THRESHOLD_PX = 5;" in source
    assert "pending: true" in source
    assert "active: false" in source
    assert "pixelDistance < DRAG_THRESHOLD_PX" in source
    assert "state.suppressClick = true" in source
    assert "if (state.suppressClick) return;" in source


def test_component_drag_formula_is_cumulative_from_scene_origin() -> None:
    source = app_js()
    assert "sceneOriginX" in source
    assert "sceneOriginY" in source
    assert "component.dataset.placementX" in source
    assert "component.dataset.placementY" in source
    assert "screenCtmInverse(svg)" in source
    assert "clientDeltaToSvgDelta(evt, state.drag)" in source
    assert "state.drag.layoutStartX + delta.x" in source
    assert "state.drag.layoutStartY + delta.y" in source
    assert "formatNumber(nx - state.drag.sceneOriginX)" in source
    assert "formatNumber(ny - state.drag.sceneOriginY)" in source
    assert "Number.isFinite(nx)" in source
    assert "Number.isFinite(ny)" in source
    assert "readTranslate(component)" in source
    assert "capturePointer(svg, evt.pointerId)" in source
    assert "releasePointer(state.drag?.captureTarget, state.drag?.pointerId)" in source


def test_child_scene_elements_do_not_start_component_drag() -> None:
    source = app_js()
    assert "NON_COMPONENT_DRAG_KINDS" in source
    for kind in [
        "pin",
        "wire",
        "wire_stub",
        "junction",
        "net_label",
        "net_label_endpoint",
        "power_symbol",
        "ground_symbol",
        "power_label",
        "ground_label",
    ]:
        assert f'"{kind}"' in source
    assert "sceneElementForTarget(evt.target)" in source
    assert "draggableComponentForTarget(evt.target, sceneTarget)" in source
    assert "NON_COMPONENT_DRAG_KINDS.has(sceneElement.kind)" in source


def test_reroute_replaces_complete_scene_state_through_apply_schematic() -> None:
    source = app_js()
    assert 'fetchJson("/api/layout/autoroute"' in source
    assert "applySchematic(routed);" in source
    assert "state.scene = schematic.scene || null;" in source
    assert "state.sceneById = indexScene(state.scene);" in source
    assert "state.drag = null;" in source
    assert "state.suppressClick = false;" in source


def test_layout_save_reports_failures_without_marking_layout_clean() -> None:
    source = app_js()
    assert 'fetchJson("/api/layout/save"' in source
    assert "statusEl.textContent = \"Layout saved\";" in source
    assert "statusEl.textContent = `Save failed: ${err.message}`;" in source
    assert "state.current.layout_dirty = true;" in source
