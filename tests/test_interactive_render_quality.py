from __future__ import annotations

from pathlib import Path

import pytest

from circuit_netlist import app as app_module
from circuit_netlist.app import LayoutSaveRequest, LoadCaseRequest
from circuit_netlist.circuit_audit import AuditCase, CircuitAuditRunner
from circuit_netlist.component_library import load_component_library
from circuit_netlist.models import Circuit, ComponentInstance, Layout, Net, NetRouteStyle, PinRef, Placement, RenderQualityMode
from circuit_netlist.router import ManhattanRouter


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def restore_current_state():
    saved_state = app_module.CURRENT_STATE.model_copy(deep=True)
    app_module.invalidate_render_cache()
    yield
    app_module.CURRENT_STATE = saved_state
    app_module.invalidate_render_cache()


def close_two_pin_circuit() -> tuple[Circuit, Layout]:
    circuit = Circuit(
        name="InteractiveQuality",
        components=[
            ComponentInstance(ref="R1", component_id="BASIC_RESISTOR"),
            ComponentInstance(ref="R2", component_id="BASIC_RESISTOR"),
        ],
        nets=[Net(name="LOCAL", pins=[PinRef(component_ref="R1", pin_name="2"), PinRef(component_ref="R2", pin_name="1")])],
    )
    layout = Layout(components={"R1": Placement(x=100, y=100), "R2": Placement(x=300, y=100)})
    return circuit, layout


def prepare_default_solar_without_layout() -> None:
    app_module.CURRENT_STATE.current_circuit_id = "example_solar_led"
    app_module.CURRENT_STATE.current_circuit_name = "Solar_LED_Controller"
    app_module.CURRENT_STATE.source_kind = "example"
    app_module.CURRENT_STATE.source_filename = "examples/solar_led.cnet"
    app_module.CURRENT_STATE.source_text = (ROOT / "examples" / "solar_led.cnet").read_text(encoding="utf-8")
    app_module.CURRENT_STATE.layout_text = None
    app_module.CURRENT_STATE.case_id = "example_solar_led"
    app_module.CURRENT_STATE.layout_dirty = False
    app_module.invalidate_render_cache()


def count_router_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    base_router_for_quality = app_module.router_for_quality
    calls = {"count": 0}

    class CountingRouter:
        def __init__(self, quality):
            self.quality = quality

        def route(self, circuit, library, layout):
            calls["count"] += 1
            return base_router_for_quality(self.quality).route(circuit, library, layout)

    monkeypatch.setattr(app_module, "router_for_quality", lambda quality: CountingRouter(quality))
    return calls


def test_router_quality_factories_set_candidate_validation_modes() -> None:
    assert ManhattanRouter.for_interactive().validate_auto_route_styles is False
    assert ManhattanRouter.for_interactive().max_auto_validated_nets == 0
    assert ManhattanRouter.for_strict().validate_auto_route_styles is True
    assert ManhattanRouter.for_strict().max_auto_validated_nets == 12
    assert ManhattanRouter.for_audit().validate_auto_route_styles is True
    assert ManhattanRouter.for_audit().max_auto_validated_nets >= ManhattanRouter.for_strict().max_auto_validated_nets


def test_interactive_router_does_not_run_scene_validated_candidate_search(monkeypatch: pytest.MonkeyPatch) -> None:
    library = load_component_library(ROOT / "components")
    circuit, layout = close_two_pin_circuit()

    def fail_candidate_search(*args, **kwargs):
        raise AssertionError("interactive route should not candidate-validate AUTO nets")

    monkeypatch.setattr(ManhattanRouter, "_validate_auto_route_styles", fail_candidate_search)
    route = ManhattanRouter.for_interactive().route(circuit, library, layout)[0]
    assert route.metadata["route_style"]["selected_style"] == "direct"
    assert "candidate_validation" not in route.metadata["route_style"]


def test_strict_router_runs_scene_validated_candidate_search(monkeypatch: pytest.MonkeyPatch) -> None:
    library = load_component_library(ROOT / "components")
    circuit, layout = close_two_pin_circuit()
    calls = {"count": 0}

    def count_candidate_search(self, circuit, library, layout, baseline_routes):
        calls["count"] += 1
        return {}, {}

    monkeypatch.setattr(ManhattanRouter, "_validate_auto_route_styles", count_candidate_search)
    ManhattanRouter.for_strict().route(circuit, library, layout)
    assert calls["count"] == 1


def test_manual_route_style_override_still_works_in_interactive_mode() -> None:
    library = load_component_library(ROOT / "components")
    circuit, layout = close_two_pin_circuit()
    layout.nets["LOCAL"] = {"route_style": NetRouteStyle.LABEL.value}
    route = ManhattanRouter.for_interactive().route(circuit, library, layout)[0]
    assert route.render_style == "net_label"
    assert route.metadata["route_style"]["manual_override"] == "label"


def test_component_and_net_details_reuse_valid_current_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    app_module.invalidate_render_cache()
    app_module.load_case(LoadCaseRequest(case_id="example_solar_led"))

    def fail_router(*args, **kwargs):
        raise AssertionError("detail endpoint should reuse cached render context")

    monkeypatch.setattr(app_module, "router_for_quality", fail_router)
    component = app_module.current_component_details("CHG1")
    net = app_module.current_net_details("VBAT")
    assert component["ref"] == "CHG1"
    assert net["net_name"] == "VBAT"


def test_default_solar_without_layout_caches_second_current_context(monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_default_solar_without_layout()
    calls = count_router_calls(monkeypatch)

    first = app_module.current_render_context()
    assert first["cache_hit"] is False
    assert app_module.CURRENT_STATE.layout_text
    assert app_module.CURRENT_STATE.layout_dirty is False

    second = app_module.current_render_context()
    assert second["cache_hit"] is True
    assert calls["count"] == 1


def test_initial_circuit_response_stores_generated_layout_without_dirty_flag() -> None:
    prepare_default_solar_without_layout()
    response = app_module.get_circuit()
    assert response["layout"]["components"]
    assert not [diag.get("code") for diag in response["diagnostics"] if diag.get("code")]
    assert app_module.CURRENT_STATE.layout_text
    assert app_module.CURRENT_STATE.layout_dirty is False
    assert response["current"]["layout_text"]
    assert response["current"]["layout_dirty"] is False


def test_default_solar_detail_and_scene_endpoints_reuse_no_layout_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_default_solar_without_layout()
    first = app_module.current_render_context()
    assert first["cache_hit"] is False

    def fail_router(*args, **kwargs):
        raise AssertionError("endpoint should reuse cached default-solar context")

    monkeypatch.setattr(app_module, "router_for_quality", fail_router)
    assert app_module.current_component_details("CHG1")["ref"] == "CHG1"
    assert app_module.current_net_details("VBAT")["net_name"] == "VBAT"
    scene_payload = app_module.current_scene_payload()
    assert scene_payload["cache_hit"] is True
    assert scene_payload["scene"]["scene_version"] == "1.0"


def test_default_solar_interactive_load_is_drc_clean() -> None:
    response = app_module.load_case(LoadCaseRequest(case_id="example_solar_led"))
    assert response["expected"]["match"] is True
    assert not [diag.get("code") for diag in response["drc"]]
    assert "DRC_POWER_SYMBOL_OVERLAP" not in response["expected"]["actual_codes"]
    assert "DRC_STUB_LABEL_OVERLAP" not in response["expected"]["actual_codes"]


def test_esp32_i2c_sensor_interactive_load_is_drc_and_connectivity_clean() -> None:
    response = app_module.load_case(LoadCaseRequest(case_id="07_esp32_i2c_sensor_good"))
    assert response["expected"]["match"] is True
    assert response["layout_loaded"] is True
    assert not [diag.get("code") for diag in response["drc"]]
    assert not [diag.get("code") for diag in response["connectivity"]]


def test_generated_layout_state_does_not_overwrite_user_layout() -> None:
    prepare_default_solar_without_layout()
    app_module.current_render_context()
    generated = app_module.CURRENT_STATE.layout_text
    assert generated

    app_module.CURRENT_STATE.layout_text = generated
    app_module.CURRENT_STATE.layout_dirty = True
    app_module.current_render_context()
    assert app_module.CURRENT_STATE.layout_text == generated
    assert app_module.CURRENT_STATE.layout_dirty is True


def test_render_cache_key_includes_layout_route_style_source_and_quality(monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_default_solar_without_layout()
    calls = count_router_calls(monkeypatch)
    app_module.current_render_context()

    layout = Layout.model_validate_json(app_module.CURRENT_STATE.layout_text or "{}")
    layout.components["CHG1"].x += 40
    app_module.CURRENT_STATE.layout_text = layout.model_dump_json(indent=2)
    assert app_module.current_render_context()["cache_hit"] is False

    layout.nets["VBAT"] = {"route_style": NetRouteStyle.LABEL.value}
    app_module.CURRENT_STATE.layout_text = layout.model_dump_json(indent=2)
    assert app_module.current_render_context()["cache_hit"] is False

    app_module.CURRENT_STATE.source_text = (app_module.CURRENT_STATE.source_text or "").replace("Solar_LED_Controller", "Solar_LED_Controller_Copy")
    assert app_module.current_render_context()["cache_hit"] is False

    simple_text = """CIRCUIT CacheQuality
COMPONENT R1 BASIC_RESISTOR
COMPONENT R2 BASIC_RESISTOR
NET LOCAL:
    R1.2
    R2.1
"""
    simple_layout = Layout(components={"R1": Placement(x=100, y=100), "R2": Placement(x=300, y=100)}).model_dump_json()
    app_module.invalidate_render_cache()
    assert app_module.render_pipeline(simple_text, "cache_quality.cnet", simple_layout, quality=RenderQualityMode.INTERACTIVE)["cache_hit"] is False
    assert app_module.render_pipeline(simple_text, "cache_quality.cnet", simple_layout, quality=RenderQualityMode.STRICT)["cache_hit"] is False
    assert app_module.render_pipeline(simple_text, "cache_quality.cnet", simple_layout, quality=RenderQualityMode.STRICT)["cache_hit"] is True
    assert calls["count"] >= 4


def test_autoroute_interactive_mode_routes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = app_module.load_case(LoadCaseRequest(case_id="example_solar_led"))
    layout = Layout.model_validate(loaded["schematic"]["layout"])
    calls = {"count": 0}

    class CountingRouter:
        def route(self, circuit, library, layout):
            calls["count"] += 1
            return ManhattanRouter.for_interactive().route(circuit, library, layout)

    def counting_router_for_quality(quality):
        assert RenderQualityMode(quality) == RenderQualityMode.INTERACTIVE
        return CountingRouter()

    monkeypatch.setattr(app_module, "router_for_quality", counting_router_for_quality)
    response = app_module.autoroute(LayoutSaveRequest(layout=layout))
    assert calls["count"] == 1
    timing = response["metrics"]["timing"]
    assert timing["quality_mode"] == "interactive"
    assert timing["route_time_ms"] >= 0
    assert timing["scene_route_element_time_ms"] >= 0
    assert timing["scene_component_time_ms"] >= 0
    assert "label_candidate_count" in timing
    assert "power_candidate_count" in timing
    assert "collision_check_count" in timing


@pytest.mark.audit
@pytest.mark.slow
def test_circuit_audit_uses_audit_router_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = {"count": 0}

    class FastAuditRouter:
        def route(self, circuit, library, layout):
            return ManhattanRouter.for_interactive().route(circuit, library, layout)

    def count_for_audit(cls):
        calls["count"] += 1
        return FastAuditRouter()

    monkeypatch.setattr(ManhattanRouter, "for_audit", classmethod(count_for_audit))
    case = AuditCase(
        id="example_solar_led",
        name="Solar LED Controller",
        path="examples/solar_led.cnet",
        classification="clean",
        topology_family="solar",
        purpose="mode split test",
    )
    result = CircuitAuditRunner(tmp_path / "audit", validate_route_styles=True).run_case(case)
    assert calls["count"] == 1
    assert result.stages["drc"] == "pass"
