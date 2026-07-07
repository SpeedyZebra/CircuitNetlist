from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from circuit_netlist.circuit_audit import (
    CLEAN_MANIFEST,
    NEGATIVE_MANIFEST,
    CircuitAuditRunner,
    audit_summary,
    load_audit_cases,
    load_manifest_cases,
    main,
)


ROOT = Path(__file__).resolve().parents[1]
CLEAN_CASES = load_manifest_cases(CLEAN_MANIFEST, "clean")
NEGATIVE_CASES = load_manifest_cases(NEGATIVE_MANIFEST, "negative")
pytestmark = [pytest.mark.audit, pytest.mark.slow]


ROUTE_VALIDATED_CASES = {
    "02_mosfet_switch_good",
    "03_voltage_divider_adc_good",
    "04_multi_rc_filter",
    "04_multi_decoupling_with_rc_filters",
    "05_relay_flyback_good",
    "07_esp32_i2c_sensor_good",
    "10_lm393_comparator_hysteresis_good",
    "12_l293d_motor_driver_good",
    "03_divider_missing_bottom",
    "erc_battery_polarity_reversed",
}


def _runner(tmp_path: Path, case_id: str) -> CircuitAuditRunner:
    return CircuitAuditRunner(tmp_path / "audit" / case_id, validate_route_styles=case_id in ROUTE_VALIDATED_CASES)


def test_audit_manifests_inventory_all_physical_cnet_files() -> None:
    manifest_paths = {case.path for case in load_audit_cases()}
    physical_paths = {str(path.relative_to(ROOT)).replace("\\", "/") for path in ROOT.rglob("*.cnet")}
    assert physical_paths == manifest_paths


def test_inventory_document_distinguishes_physical_fixtures_from_embedded_netlists() -> None:
    inventory = (ROOT / "docs" / "circuit_inventory.md").read_text(encoding="utf-8")
    assert "Physical Circuit Files" in inventory
    assert "Embedded Test Netlists" in inventory
    assert "not user-facing project fixtures" in inventory
    assert "no | yes | no" in inventory


def test_clean_manifest_contains_required_user_facing_families() -> None:
    families = {case.topology_family for case in CLEAN_CASES}
    assert "op-amp" in families
    assert "555 timer" in families
    assert "motor or relay with flyback" in families
    assert "solar LED / solar charger battery system" in families
    assert "repeated MOSFET channels" in families
    assert "MCU MOSFET PWM driver" in families
    assert "MCU I2C sensor" in families
    assert "MCU SPI ADC" in families
    assert "level-shifted LED strip" in families


@pytest.mark.parametrize("case", CLEAN_CASES, ids=lambda case: case.id)
def test_clean_circuit_full_pipeline_has_zero_diagnostics(case, tmp_path: Path) -> None:
    result = _runner(tmp_path, case.id).run_case(case)
    assert result.overall == "pass", result.diagnostics
    assert result.classification == "clean"
    assert result.actual_codes == []
    assert result.stages["parse"] == "pass"
    assert result.stages["validation"] == "pass"
    assert result.stages["topology"] == "pass"
    assert result.stages["placement"] == "pass"
    assert result.stages["routing"] == "pass"
    assert result.stages["scene"] == "pass"
    assert result.stages["drc"] == "pass"
    assert result.stages["rendered_connectivity"] == "pass"
    assert result.stages["erc"] == "pass"
    assert result.stages["svg_export"] == "pass"
    assert result.stages["png_export"] in {"pass", "unavailable"}
    assert result.metrics["scene_elements"] > 0
    assert result.metrics["scene_width"] > 0
    assert result.metrics["scene_height"] > 0


@pytest.mark.parametrize("case", NEGATIVE_CASES, ids=lambda case: case.id)
def test_negative_circuit_exact_diagnostics_only(case, tmp_path: Path) -> None:
    result = _runner(tmp_path, case.id).run_case(case)
    assert result.overall == "pass", result.diagnostics
    assert result.classification == "negative"
    assert result.actual_codes == sorted(case.expected_diagnostics)
    assert result.missing_expected_codes == []
    assert result.unexpected_codes == []
    assert result.stages["parse"] == "pass"
    assert result.stages["validation"] == "pass"
    if case.render_allowed:
        assert result.stages["scene"] == "pass"
        assert result.stages["drc"] == "pass"
        assert result.stages["rendered_connectivity"] in {"pass", "fail"}
        assert result.stages["svg_export"] == "pass"


def test_audit_cli_writes_json_and_markdown_reports(tmp_path: Path) -> None:
    output = tmp_path / "audit"
    assert main(["--all", "--output", str(output)]) == 0
    assert (output / "report.json").exists()
    assert (output / "report.md").exists()
    assert list((output / "svg").glob("*.svg"))
    assert list((output / "png").glob("*.png"))


def test_single_case_audit_relative_output_path_has_valid_export_paths() -> None:
    output = Path(".pytest-tmp") / "audit_relative_single"
    assert main(["--case", "example_solar_led", "--output", str(output)]) == 0
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    case = report["cases"][0]
    assert case["svg_path"]
    assert case["png_path"]
    assert (ROOT / case["svg_path"]).exists()
    assert (ROOT / case["png_path"]).exists()
    assert "EXPORT_SVG_FAILED" not in case["actual_codes"]
    assert "EXPORT_PNG_FAILED" not in case["actual_codes"]


def test_single_case_audit_absolute_output_path_has_valid_export_paths(tmp_path: Path) -> None:
    output = (tmp_path / "absolute_audit").resolve()
    assert main(["--case", "example_solar_led", "--output", str(output)]) == 0
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    case = report["cases"][0]
    assert (ROOT / case["svg_path"]).exists() if not Path(case["svg_path"]).is_absolute() else Path(case["svg_path"]).exists()
    assert (ROOT / case["png_path"]).exists() if not Path(case["png_path"]).is_absolute() else Path(case["png_path"]).exists()
    assert "EXPORT_SVG_FAILED" not in case["actual_codes"]
    assert "EXPORT_PNG_FAILED" not in case["actual_codes"]


def test_single_case_audit_output_path_outside_repo_has_valid_absolute_paths() -> None:
    with tempfile.TemporaryDirectory(prefix="circuit_netlist_audit_") as directory:
        output = Path(directory) / "outside_repo"
        assert main(["--case", "example_solar_led", "--output", str(output)]) == 0
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        case = report["cases"][0]
        assert Path(case["svg_path"]).is_absolute()
        assert Path(case["png_path"]).is_absolute()
        assert Path(case["svg_path"]).exists()
        assert Path(case["png_path"]).exists()
        assert "EXPORT_SVG_FAILED" not in case["actual_codes"]
        assert "EXPORT_PNG_FAILED" not in case["actual_codes"]


def test_audit_summary_reports_zero_unexpected_diagnostics(tmp_path: Path) -> None:
    runner = CircuitAuditRunner(tmp_path / "audit")
    results = runner.run(load_audit_cases())
    summary = audit_summary(results)
    assert summary["total_circuits"] == 45
    assert summary["clean_circuits"] == 21
    assert summary["negative_circuits"] == 24
    assert summary["passed_clean_circuits"] == 21
    assert summary["passed_negative_circuits"] == 24
    assert summary["unexpected_diagnostics"] == 0
