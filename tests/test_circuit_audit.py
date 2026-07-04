from __future__ import annotations

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


def _runner(tmp_path: Path, case_id: str) -> CircuitAuditRunner:
    return CircuitAuditRunner(tmp_path / "audit" / case_id)


def test_audit_manifests_inventory_all_physical_cnet_files() -> None:
    manifest_paths = {case.path for case in load_audit_cases()}
    physical_paths = {str(path.relative_to(ROOT)).replace("\\", "/") for path in ROOT.rglob("*.cnet")}
    assert physical_paths == manifest_paths


def test_clean_manifest_contains_required_user_facing_families() -> None:
    families = {case.topology_family for case in CLEAN_CASES}
    assert "op-amp" in families
    assert "555 timer" in families
    assert "motor or relay with flyback" in families
    assert "solar LED / solar charger battery system" in families
    assert "repeated MOSFET channels" in families


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
        assert result.stages["svg_export"] == "pass"


def test_audit_cli_writes_json_and_markdown_reports(tmp_path: Path) -> None:
    output = tmp_path / "audit"
    assert main(["--all", "--output", str(output)]) == 0
    assert (output / "report.json").exists()
    assert (output / "report.md").exists()
    assert list((output / "svg").glob("*.svg"))
    assert list((output / "png").glob("*.png"))


def test_audit_summary_reports_zero_unexpected_diagnostics(tmp_path: Path) -> None:
    runner = CircuitAuditRunner(tmp_path / "audit")
    results = runner.run(load_audit_cases())
    summary = audit_summary(results)
    assert summary["total_circuits"] == 25
    assert summary["clean_circuits"] == 13
    assert summary["negative_circuits"] == 12
    assert summary["passed_clean_circuits"] == 13
    assert summary["passed_negative_circuits"] == 12
    assert summary["unexpected_diagnostics"] == 0
