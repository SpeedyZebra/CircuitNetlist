import pytest
from fastapi import HTTPException

from circuit_netlist import app as app_module
from circuit_netlist.app import LayoutSaveRequest, LoadCaseRequest, LoadTextRequest
from circuit_netlist.models import Layout


pytestmark = [pytest.mark.integration, pytest.mark.slow]


VALID_TEXT = """CIRCUIT Uploaded_LED

COMPONENT V1 POWER_DC_SOURCE voltage=5V
COMPONENT R1 BASIC_RESISTOR value=330ohm
COMPONENT LED1 LIGHT_LED_RED vf=2.0V current=10mA

NET VCC:
    V1.POS
    R1.1

NET LED_ANODE:
    R1.2
    LED1.A

NET GND:
    V1.NEG
    LED1.K
"""


def test_circuit_catalog_loads_examples_and_regression_cases() -> None:
    catalog = app_module.get_circuits()
    assert catalog["examples"]
    assert any(item["id"] == "example_solar_led" for item in catalog["examples"])
    assert any(item["id"] == "example_inverting_op_amp" for item in catalog["examples"])
    assert any(item["id"] == "example_555_timer_50_duty_astable" for item in catalog["examples"])
    assert catalog["regression"]["good"]
    assert catalog["regression"]["faults"]


def test_unknown_case_and_path_traversal_are_rejected() -> None:
    with pytest.raises(HTTPException):
        app_module.get_circuit_metadata("does_not_exist")
    with pytest.raises(HTTPException):
        app_module.load_case(LoadCaseRequest(case_id="../../README.md"))


def test_valid_uploaded_cnet_text_loads_and_updates_current_state() -> None:
    response = app_module.load_text(LoadTextRequest(filename="uploaded.cnet", text=VALID_TEXT))
    assert response["success"] is True
    assert response["circuit_name"] == "Uploaded_LED"
    current = app_module.current_circuit()
    assert current["source_kind"] == "uploaded"
    assert current["current_circuit_name"] == "Uploaded_LED"


def test_invalid_uploaded_netlist_returns_structured_errors() -> None:
    response = app_module.load_text(LoadTextRequest(filename="bad.cnet", text="CIRCUIT Bad\nCOMPONENT R1 BASIC_RESISTOR value=1kohm\nNET X:\n    R1.NOPE\n    R1.2\n"))
    assert response["success"] is False
    assert any(diag["code"] == "VALIDATION_UNKNOWN_PIN" for diag in response["diagnostics"])


def test_matching_layout_loads_when_available_and_reload_works_for_builtin() -> None:
    response = app_module.load_case(LoadCaseRequest(case_id="01_led_resistor_good"))
    assert response["success"] is True
    assert response["layout_loaded"] is True
    reloaded = app_module.reload_current()
    assert reloaded["success"] is True
    assert reloaded["circuit_name"] == response["circuit_name"]


def test_inverting_op_amp_example_loads_from_catalog() -> None:
    response = app_module.load_case(LoadCaseRequest(case_id="example_inverting_op_amp"))
    assert response["success"] is True
    assert response["layout_loaded"] is False
    assert response["circuit_name"] == "Inverting_Op_Amp"
    assert "BASIC_OP_AMP" in response["schematic"]["svg"]
    assert "net-VOUT" in response["schematic"]["svg"]
    assert not any(diag.get("severity") in {"ERROR", "FATAL"} for diag in response["diagnostics"])


def test_555_timer_example_loads_from_catalog_with_saved_layout() -> None:
    response = app_module.load_case(LoadCaseRequest(case_id="example_555_timer_50_duty_astable"))
    assert response["success"] is True
    assert response["layout_loaded"] is True
    assert response["circuit_name"] == "Timer_555_50_Duty_Astable"
    assert "BASIC_555_TIMER" in response["schematic"]["svg"]
    assert "timer-555-body" in response["schematic"]["svg"]
    assert "net-OUT" in response["schematic"]["svg"]
    assert not any(diag.get("severity") in {"ERROR", "FATAL"} for diag in response["diagnostics"])
    nets = {
        net["name"]: {(pin["component_ref"], pin["pin_name"]) for pin in net["pins"]}
        for net in response["schematic"]["circuit"]["nets"]
    }
    assert ("R2", "1") in nets["OUT"]
    assert ("R2", "2") in nets["TIMING"]
    assert ("R2", "1") not in nets["VCC"]
    assert {("U1", "TRIG"), ("U1", "THRESH"), ("C1", "1"), ("R2", "2")} <= nets["TIMING"]


def test_missing_layout_triggers_autoplacement_for_uploaded_text_and_reload_uses_memory() -> None:
    response = app_module.load_text(LoadTextRequest(filename="memory.cnet", text=VALID_TEXT))
    assert response["success"] is True
    assert response["layout_loaded"] is False
    assert response["schematic"]["layout"]["components"]
    reloaded = app_module.reload_current()
    assert reloaded["success"] is True
    assert reloaded["filename"] == "memory.cnet"


def test_autoroute_returns_complete_scene_state_for_frontend_refresh() -> None:
    loaded = app_module.load_text(LoadTextRequest(filename="memory.cnet", text=VALID_TEXT))
    layout = Layout.model_validate(loaded["schematic"]["layout"])
    routed = app_module.autoroute(LayoutSaveRequest(layout=layout))
    assert routed["layout"]["components"]
    assert routed["scene"]["scene_version"] == "1.0"
    assert routed["schematic"]["scene"] == routed["scene"]
    assert routed["schematic"]["layout"] == routed["layout"]
    assert 'data-scene-version="1.0"' in routed["svg"]


def test_placement_score_endpoint_returns_current_optimizer_summary() -> None:
    app_module.load_case(LoadCaseRequest(case_id="example_solar_led"))
    payload = app_module.current_placement_score()
    assert payload["layout_available"] is True
    assert payload["placement_optimizer"]["mode"] == "off"
    assert payload["quality_mode"] == "interactive"
    strict_payload = app_module.current_placement_score(quality="strict")
    assert strict_payload["placement_optimizer"]["mode"] == "optimize"
    assert payload["placement_optimizer"]["optimized"]["hard_violation_count"] == 0
    assert "optimized_routed" in payload["placement_optimizer"]
    assert "route_validations" in payload["placement_optimizer"]
    assert payload["placement_optimizer"]["optimized_routed"] is None
    assert strict_payload["placement_optimizer"]["optimized_routed"]["routing_succeeded"] is True


def test_regression_expected_summary_for_good_and_fault_cases() -> None:
    good = app_module.load_case(LoadCaseRequest(case_id="01_led_resistor_good"))
    fault = app_module.load_case(LoadCaseRequest(case_id="01_led_missing_resistor"))
    assert good["expected"]["codes"] == []
    assert good["expected"]["match"] is True
    assert "ERC_LED_NO_CURRENT_LIMIT" in fault["expected"]["codes"]
    assert fault["expected"]["match"] is True
