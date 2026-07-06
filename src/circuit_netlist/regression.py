from __future__ import annotations

import argparse
import html
import json
import sys
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .component_library import load_component_library
from .diagnostics import compare_diagnostic_codes
from .erc import can_run_electrical_rules, run_electrical_rules
from .exporters import export_svg
from .models import Circuit, Diagnostic, EngineeringValue, Layout, Severity
from .parser import NetlistParser
from .placement import DeterministicPlacementEngine
from .rendered_connectivity import validate_rendered_connectivity
from .renderer import render_circuit
from .router import ManhattanRouter
from .scene_builder import build_schematic_scene
from .validator import CircuitValidator, has_blocking_diagnostics
from .visual_drc import visual_drc, visual_drc_from_scene, wire_like_segments, wire_like_segments_from_scene


ROOT = Path(__file__).resolve().parents[2]
SUITE_ROOT = ROOT / "test_circuits"
GENERATED_ROOT = SUITE_ROOT / "generated"


@dataclass
class CaseResult:
    case_id: str
    family: str
    case_type: str
    path: str
    parse: str = "fail"
    validation: str = "fail"
    erc: str = "skip"
    drc: str = "skip"
    connectivity: str = "skip"
    render: str = "skip"
    expected_match: bool = False
    overall: str = "fail"
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    expected_codes: list[str] = field(default_factory=list)
    actual_codes: list[str] = field(default_factory=list)
    missing_expected_codes: list[str] = field(default_factory=list)
    unexpected_codes: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    patterns: list[dict[str, Any]] = field(default_factory=list)
    calculations: list[dict[str, Any]] = field(default_factory=list)
    svg_path: str | None = None
    json_path: str | None = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Circuit Netlist regression cases.")
    parser.add_argument("--all", action="store_true", help="Run all manifest cases.")
    parser.add_argument("--good-only", action="store_true", help="Run only good cases.")
    parser.add_argument("--faults-only", action="store_true", help="Run only fault cases.")
    parser.add_argument("--case", help="Run one case id or family substring.")
    parser.add_argument("--update-baseline", action="store_true", help="Reserved for future baseline updates.")
    parser.add_argument("--open-report", action="store_true", help="Open generated HTML report.")
    args = parser.parse_args(argv)

    results = RegressionRunner().run(args)
    report = build_report(results)
    if args.open_report:
        webbrowser.open(report.as_uri())
    failures = [result for result in results if result.overall != "pass"]
    print(f"Regression complete: {len(results) - len(failures)}/{len(results)} passed")
    print(f"Report: {report}")
    return 1 if failures else 0


class RegressionRunner:
    def __init__(self, suite_root: Path = SUITE_ROOT) -> None:
        self.suite_root = suite_root
        self.library = load_component_library(ROOT / "components")
        self.parser = NetlistParser()

    def run(self, args: argparse.Namespace) -> list[CaseResult]:
        manifest = self._load_manifest()
        expected = self._load_expected()
        cases = self._filter_cases(manifest.get("cases", []), args)
        self._prepare_output_dirs()
        return [self._run_case(case, expected.get(case["path"], {})) for case in cases]

    def _load_manifest(self) -> dict[str, Any]:
        return yaml.safe_load((self.suite_root / "manifest.yaml").read_text(encoding="utf-8"))

    def _load_expected(self) -> dict[str, Any]:
        path = self.suite_root / "expected" / "expected_results.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data.get("circuits", {})

    def _filter_cases(self, cases: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
        if args.good_only:
            cases = [case for case in cases if case.get("type") == "good"]
        if args.faults_only:
            cases = [case for case in cases if case.get("type") == "fault"]
        if args.case:
            needle = args.case.lower()
            cases = [case for case in cases if needle in case.get("id", "").lower() or needle in case.get("family", "").lower()]
        return cases

    def _prepare_output_dirs(self) -> None:
        for name in ("svg", "png", "json", "report"):
            (GENERATED_ROOT / name).mkdir(parents=True, exist_ok=True)

    def _run_case(self, case: dict[str, Any], expected: dict[str, Any]) -> CaseResult:
        result = CaseResult(case_id=case["id"], family=case["family"], case_type=case["type"], path=case["path"])
        netlist_path = self.suite_root / case["path"]
        circuit, diagnostics = self.parser.parse_file(netlist_path)
        result.parse = "pass" if circuit else "fail"
        if circuit is None:
            return self._finalize(result, diagnostics, expected)

        validation = CircuitValidator(self.library).validate(circuit)
        diagnostics.extend(validation)
        result.validation = "fail" if has_blocking_diagnostics(validation) else "pass"

        if can_run_electrical_rules(validation):
            diagnostics.extend(run_electrical_rules(circuit, self.library))
            result.erc = "pass" if not has_blocking_diagnostics(validation) else "partial"
        else:
            result.erc = "blocked"

        render_allowed = bool(expected.get("render_allowed", not has_blocking_diagnostics(diagnostics)))
        if render_allowed and circuit:
            layout = self._case_layout(case, circuit)
            routes = ManhattanRouter.for_strict().route(circuit, self.library, layout)
            scene = build_schematic_scene(circuit, self.library, layout, routes)
            drc_diagnostics, metrics = visual_drc_from_scene(scene)
            connectivity_diagnostics, connectivity_metrics = validate_rendered_connectivity(circuit, scene)
            diagnostics.extend(drc_diagnostics)
            diagnostics.extend(connectivity_diagnostics)
            result.metrics = {**metrics, "rendered_connectivity": connectivity_metrics}
            result.drc = "pass" if not any(diag.severity in {Severity.ERROR, Severity.FATAL} for diag in drc_diagnostics) else "fail"
            result.connectivity = "pass" if not any(diag.severity in {Severity.ERROR, Severity.FATAL} for diag in connectivity_diagnostics) else "fail"
            svg = render_circuit(circuit, self.library, layout, routes, diagnostics)
            svg_path = GENERATED_ROOT / "svg" / f"{case['id']}.svg"
            export_svg(svg, svg_path)
            result.svg_path = str(svg_path.relative_to(SUITE_ROOT))
            result.render = "pass"
            result.patterns = detect_patterns(circuit, self.library)
            result.calculations = engineering_calculations(circuit, self.library)
        return self._finalize(result, diagnostics, expected)

    def _case_layout(self, case: dict[str, Any], circuit: Circuit) -> Layout:
        path = (self.suite_root / case["path"]).with_name("circuit.layout.json")
        existing = Layout.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None
        return DeterministicPlacementEngine().place(circuit, self.library, existing)

    def _finalize(self, result: CaseResult, diagnostics: list[Diagnostic], expected: dict[str, Any]) -> CaseResult:
        expected_codes = list(expected.get("expected_codes", []))
        actual_codes = sorted(diag.code for diag in diagnostics if diag.code)
        result.diagnostics = [diag.model_dump(mode="json") for diag in diagnostics]
        result.expected_codes = expected_codes
        result.actual_codes = actual_codes
        missing, unexpected, matched = compare_diagnostic_codes(expected_codes, actual_codes, list(expected.get("allowed_codes", [])))
        result.missing_expected_codes = missing
        result.unexpected_codes = unexpected
        result.expected_match = matched
        expected_parse = expected.get("parse", "pass")
        expected_validation = expected.get("validation", "pass")
        stage_match = result.parse == expected_parse and (expected_validation == "any" or result.validation == expected_validation)
        result.overall = "pass" if result.expected_match and stage_match else "fail"
        json_path = GENERATED_ROOT / "json" / f"{result.case_id}.json"
        json_path.write_text(json.dumps(result.__dict__, indent=2), encoding="utf-8")
        result.json_path = str(json_path.relative_to(SUITE_ROOT))
        return result


def detect_patterns(circuit: Circuit, library: Any) -> list[dict[str, Any]]:
    patterns: list[dict[str, Any]] = []
    for component in circuit.components:
        role = role_of(component)
        if role in {"current_limit", "pulldown", "divider_top", "divider_bottom"}:
            patterns.append({"pattern": role, "status": "detected", "components": [component.ref], "nets": sorted(component_nets(circuit, component.ref))})
    mosfets = [component.ref for component in circuit.components if component.component_id == "BASIC_NMOS"]
    if mosfets:
        patterns.append({"pattern": "low_side_mosfet_switch", "status": "detected", "components": mosfets, "nets": []})
    return patterns


def engineering_calculations(circuit: Circuit, library: Any) -> list[dict[str, Any]]:
    calculations: list[dict[str, Any]] = []
    for led in [component for component in circuit.components if component.component_id.startswith("LIGHT_LED_") and component.component_id != "LIGHT_LED_RGB_ADDRESSABLE"]:
        resistor = next((component for component in circuit.components if role_of(component) == "current_limit"), None)
        source = next((component for component in circuit.components if component.component_id == "POWER_DC_SOURCE"), None)
        if not resistor or not source:
            continue
        voltage = numeric_param(source, "voltage")
        resistance = numeric_param(resistor, "value")
        vf = numeric_param(led, "vf")
        if voltage and resistance and vf is not None:
            calculations.append({"calculation": "led_current", "components": [source.ref, resistor.ref, led.ref], "value_a": (voltage - vf) / resistance, "assumption": "Ideal DC source, resistor, and LED forward voltage."})
    top = next((component for component in circuit.components if role_of(component) == "divider_top"), None)
    bottom = next((component for component in circuit.components if role_of(component) == "divider_bottom"), None)
    source = next((component for component in circuit.components if component.component_id == "POWER_DC_SOURCE"), None)
    if top and bottom and source:
        vin = numeric_param(source, "voltage")
        r_top = numeric_param(top, "value")
        r_bottom = numeric_param(bottom, "value")
        if vin and r_top and r_bottom:
            calculations.append({"calculation": "voltage_divider", "components": [top.ref, bottom.ref], "value_v": vin * r_bottom / (r_top + r_bottom), "assumption": "Unloaded ideal divider."})
    return calculations


def component_nets(circuit: Circuit, ref: str) -> set[str]:
    return {net.name for net in circuit.nets for pin in net.pins if pin.component_ref == ref}


def role_of(component: Any) -> str:
    value = component.parameters.get("role")
    return value.original if isinstance(value, EngineeringValue) else str(value or "")


def numeric_param(component: Any, name: str) -> float | None:
    value = component.parameters.get(name)
    if isinstance(value, EngineeringValue):
        return value.numeric
    return None


def build_report(results: list[CaseResult]) -> Path:
    report_dir = GENERATED_ROOT / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for result in results:
        status_class = "pass" if result.overall == "pass" else "fail"
        svg_embed = ""
        if result.svg_path:
            svg_file = SUITE_ROOT / result.svg_path
            if svg_file.exists():
                svg_embed = svg_file.read_text(encoding="utf-8")
        rows.append(
            f"<tr class='{status_class}'><td>{html.escape(result.case_id)}</td><td>{html.escape(result.case_type)}</td>"
            f"<td>{result.parse}</td><td>{result.validation}</td><td>{result.erc}</td><td>{result.drc}</td><td>{result.render}</td>"
            f"<td>{'pass' if result.expected_match else 'fail'}</td><td>{result.overall}</td></tr>"
            f"<tr class='detail'><td colspan='9'><details><summary>Details</summary>"
            f"<p><strong>Expected:</strong> {html.escape(', '.join(result.expected_codes) or 'none')}</p>"
            f"<p><strong>Actual:</strong> {html.escape(', '.join(result.actual_codes) or 'none')}</p>"
            f"<p><strong>Missing:</strong> {html.escape(', '.join(result.missing_expected_codes) or 'none')}</p>"
            f"<p><strong>Unexpected:</strong> {html.escape(', '.join(result.unexpected_codes) or 'none')}</p>"
            f"<pre>{html.escape(json.dumps(result.metrics, indent=2))}</pre><div class='preview'>{svg_embed}</div></details></td></tr>"
        )
    passed = sum(1 for result in results if result.overall == "pass")
    html_text = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Circuit Regression Report</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;background:#10151c;color:#e6edf5;margin:24px}}
table{{border-collapse:collapse;width:100%;background:#17202b}}td,th{{border:1px solid #344255;padding:6px 8px;text-align:left}}
tr.pass{{background:#14351f}}tr.fail{{background:#421b1b}}tr.detail{{background:#111922}}.preview svg{{max-width:760px;max-height:420px;background:#0d1117}}
.summary{{font-size:18px;margin-bottom:16px}}.pass-text{{color:#7ee787}}.fail-text{{color:#ff9b9b}}
</style></head><body>
<h1>Circuit Regression Report</h1>
<div class="summary"><span class="{'pass-text' if passed == len(results) else 'fail-text'}">{passed}/{len(results)} cases passed</span></div>
<table><thead><tr><th>Case</th><th>Type</th><th>Parse</th><th>Validation</th><th>ERC</th><th>DRC</th><th>Render</th><th>Expected Match</th><th>Overall</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<h2>Known limitations</h2>
<p>This vertical slice covers the first three circuit families. Deeper op-amp, timer, motor, solar charger, and cabin-system numeric rules are not implemented yet.</p>
</body></html>"""
    path = report_dir / "index.html"
    path.write_text(html_text, encoding="utf-8")
    return path


if __name__ == "__main__":
    sys.exit(main())
