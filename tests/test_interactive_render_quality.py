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


def test_router_quality_factories_set_candidate_validation_modes() -> None:
    assert ManhattanRouter.for_interactive().validate_auto_route_styles is False
    assert ManhattanRouter.for_interactive().max_auto_validated_nets == 0
    assert ManhattanRouter.for_strict().validate_auto_route_styles is True
    assert ManhattanRouter.for_strict().max_auto_validated_nets == 2
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
    assert response["metrics"]["timing"]["quality_mode"] == "interactive"
    assert response["metrics"]["timing"]["route_time_ms"] >= 0


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
