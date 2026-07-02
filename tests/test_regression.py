from pathlib import Path

import yaml

from circuit_netlist.regression import RegressionRunner, compare_diagnostic_codes, engineering_calculations, main
from circuit_netlist.component_library import load_component_library
from circuit_netlist.parser import NetlistParser


ROOT = Path(__file__).resolve().parents[1]


def test_regression_manifest_loads() -> None:
    manifest = yaml.safe_load((ROOT / "test_circuits" / "manifest.yaml").read_text(encoding="utf-8"))
    assert len(manifest["cases"]) == 21
    assert {case["family"] for case in manifest["cases"]} >= {"01_led_resistor", "02_mosfet_switch", "03_voltage_divider_adc", "04_repeated_groups"}


def test_expected_results_schema_vertical_slice() -> None:
    expected = yaml.safe_load((ROOT / "test_circuits" / "expected" / "expected_results.yaml").read_text(encoding="utf-8"))
    circuits = expected["circuits"]
    for case in yaml.safe_load((ROOT / "test_circuits" / "manifest.yaml").read_text(encoding="utf-8"))["cases"]:
        assert case["path"] in circuits
        assert "parse" in circuits[case["path"]]
        assert "validation" in circuits[case["path"]]
        assert "expected_codes" in circuits[case["path"]]


def test_diagnostic_code_compare_fails_unexpected_drc_code() -> None:
    missing, unexpected, matched = compare_diagnostic_codes(["ERC_LED_POLARITY_REVERSED"], ["ERC_LED_POLARITY_REVERSED", "DRC_WIRE_SYMBOL_OVERLAP"])
    assert missing == []
    assert unexpected == ["DRC_WIRE_SYMBOL_OVERLAP"]
    assert matched is False


def test_diagnostic_code_compare_fails_unexpected_erc_code() -> None:
    missing, unexpected, matched = compare_diagnostic_codes([], ["ERC_LED_NO_CURRENT_LIMIT"])
    assert missing == []
    assert unexpected == ["ERC_LED_NO_CURRENT_LIMIT"]
    assert matched is False


def test_diagnostic_code_compare_fails_missing_expected_code() -> None:
    missing, unexpected, matched = compare_diagnostic_codes(["VALIDATION_UNKNOWN_PIN"], [])
    assert missing == ["VALIDATION_UNKNOWN_PIN"]
    assert unexpected == []
    assert matched is False


def test_diagnostic_code_compare_passes_matching_codes_independent_of_order() -> None:
    missing, unexpected, matched = compare_diagnostic_codes(["DRC_A", "ERC_B"], ["ERC_B", "DRC_A"])
    assert missing == []
    assert unexpected == []
    assert matched is True


def test_diagnostic_code_compare_counts_duplicates_consistently() -> None:
    missing, unexpected, matched = compare_diagnostic_codes(["DRC_A"], ["DRC_A", "DRC_A"])
    assert missing == []
    assert unexpected == ["DRC_A"]
    assert matched is False


def test_diagnostic_code_compare_does_not_silently_exclude_namespaces() -> None:
    missing, unexpected, matched = compare_diagnostic_codes([], ["DRC_X", "ERC_X", "VALIDATION_X", "PARSE_X"])
    assert missing == []
    assert unexpected == ["DRC_X", "ERC_X", "PARSE_X", "VALIDATION_X"]
    assert matched is False


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
