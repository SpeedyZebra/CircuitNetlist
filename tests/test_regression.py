from pathlib import Path

import yaml

from circuit_netlist.regression import RegressionRunner, engineering_calculations, main
from circuit_netlist.component_library import load_component_library
from circuit_netlist.parser import NetlistParser


ROOT = Path(__file__).resolve().parents[1]


def test_regression_manifest_loads() -> None:
    manifest = yaml.safe_load((ROOT / "test_circuits" / "manifest.yaml").read_text(encoding="utf-8"))
    assert len(manifest["cases"]) == 15
    assert {case["family"] for case in manifest["cases"]} >= {"01_led_resistor", "02_mosfet_switch", "03_voltage_divider_adc"}


def test_expected_results_schema_vertical_slice() -> None:
    expected = yaml.safe_load((ROOT / "test_circuits" / "expected" / "expected_results.yaml").read_text(encoding="utf-8"))
    circuits = expected["circuits"]
    for case in yaml.safe_load((ROOT / "test_circuits" / "manifest.yaml").read_text(encoding="utf-8"))["cases"]:
        assert case["path"] in circuits
        assert "parse" in circuits[case["path"]]
        assert "validation" in circuits[case["path"]]
        assert "expected_codes" in circuits[case["path"]]


def test_regression_runner_all_cases_passes_and_writes_report() -> None:
    assert main(["--all"]) == 0
    assert (ROOT / "test_circuits" / "generated" / "report" / "index.html").exists()
    assert (ROOT / "test_circuits" / "generated" / "json" / "01_led_resistor_good.json").exists()


def test_good_cases_parse_and_have_no_expected_fault_codes() -> None:
    results = RegressionRunner().run(type("Args", (), {"good_only": True, "faults_only": False, "case": None})())
    assert results
    for result in results:
        assert result.parse == "pass"
        assert result.validation == "pass"
        assert result.overall == "pass"
        assert not result.expected_codes
        assert not [code for code in result.actual_codes if code.startswith("ERC_")]


def test_required_faults_trigger_expected_codes() -> None:
    results = RegressionRunner().run(type("Args", (), {"good_only": False, "faults_only": True, "case": None})())
    assert results
    for result in results:
        assert result.overall == "pass"
        assert not result.missing_expected_codes


def test_led_and_divider_numeric_calculations() -> None:
    library = load_component_library(ROOT / "components")
    parser = NetlistParser()
    led_circuit, _ = parser.parse_file(ROOT / "test_circuits" / "good" / "01_led_resistor" / "circuit.cnet")
    divider_circuit, _ = parser.parse_file(ROOT / "test_circuits" / "good" / "03_voltage_divider_adc" / "circuit.cnet")
    led_calcs = engineering_calculations(led_circuit, library)
    divider_calcs = engineering_calculations(divider_circuit, library)
    assert any(calc["calculation"] == "led_current" and 0.008 < calc["value_a"] < 0.010 for calc in led_calcs)
    assert any(calc["calculation"] == "voltage_divider" and calc["value_v"] == 2.5 for calc in divider_calcs)
