from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

import yaml

from .component_library import ComponentLibrary, load_component_library
from .diagnostics import compare_expected_diagnostics, diagnostic_codes, diagnostic_subsystem, normalize_diagnostics
from .erc import can_run_electrical_rules, run_electrical_rules
from .exporters import export_png_from_scene, export_svg_from_scene
from .models import Circuit, Diagnostic, Layout, RenderQualityMode, RoutedNet, Severity
from .parser import NetlistParser
from .placement import DeterministicPlacementEngine
from .rendered_connectivity import validate_rendered_connectivity
from .router import ManhattanRouter
from .scene import SchematicScene
from .scene_builder import build_schematic_scene
from .topology import TopologyAnalyzer
from .validator import CircuitValidator, has_blocking_diagnostics
from .visual_drc import visual_drc_from_scene


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = ROOT / "output" / "circuit_audit"
CLEAN_MANIFEST = ROOT / "test_circuits" / "clean_circuits.yaml"
NEGATIVE_MANIFEST = ROOT / "test_circuits" / "negative_circuits.yaml"


@dataclass(frozen=True)
class AuditCase:
    id: str
    name: str
    path: str
    classification: str
    topology_family: str
    purpose: str
    source_kind: str = "regression"
    expected_diagnostics: tuple[str, ...] = ()
    allowed_diagnostics: tuple[str, ...] = ()
    render_allowed: bool = True


@dataclass
class AuditResult:
    case_id: str
    name: str
    path: str
    classification: str
    topology_family: str
    purpose: str
    stages: dict[str, str] = field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    expected_codes: list[str] = field(default_factory=list)
    actual_codes: list[str] = field(default_factory=list)
    missing_expected_codes: list[str] = field(default_factory=list)
    unexpected_codes: list[str] = field(default_factory=list)
    expected_match: bool = False
    overall: str = "fail"
    metrics: dict[str, Any] = field(default_factory=dict)
    svg_path: str | None = None
    png_path: str | None = None
    elapsed_ms: float = 0.0


def load_audit_cases(
    clean_manifest: Path = CLEAN_MANIFEST,
    negative_manifest: Path = NEGATIVE_MANIFEST,
) -> list[AuditCase]:
    return [*load_manifest_cases(clean_manifest, "clean"), *load_manifest_cases(negative_manifest, "negative")]


def load_manifest_cases(path: Path, classification: str) -> list[AuditCase]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases: list[AuditCase] = []
    for item in data.get("circuits", []):
        expected = item.get("expected_diagnostics")
        if expected is None:
            expected = item.get("diagnostics", data.get("diagnostics", []))
        cases.append(
            AuditCase(
                id=str(item["id"]),
                name=str(item.get("name", item["id"])),
                path=str(item["path"]),
                classification=classification,
                topology_family=str(item.get("topology_family", "documented")),
                purpose=str(item.get("purpose", "")),
                source_kind=str(item.get("source_kind", "regression")),
                expected_diagnostics=tuple(str(code) for code in expected),
                allowed_diagnostics=tuple(str(code) for code in item.get("allowed_diagnostics", [])),
                render_allowed=bool(item.get("render_allowed", True)),
            )
        )
    return cases


class CircuitAuditRunner:
    def __init__(self, output_root: Path = DEFAULT_OUTPUT_ROOT, library: ComponentLibrary | None = None, validate_route_styles: bool = True) -> None:
        self.output_root = output_root.expanduser().resolve()
        self.library = library or load_component_library(ROOT / "components")
        self.parser = NetlistParser()
        self.validate_route_styles = validate_route_styles

    def run(self, cases: list[AuditCase]) -> list[AuditResult]:
        self._prepare_output_dirs()
        return [self.run_case(case) for case in cases]

    def run_case(self, case: AuditCase) -> AuditResult:
        started = perf_counter()
        result = AuditResult(
            case_id=case.id,
            name=case.name,
            path=case.path,
            classification=case.classification,
            topology_family=case.topology_family,
            purpose=case.purpose,
            expected_codes=list(case.expected_diagnostics),
        )
        diagnostics: list[Diagnostic] = [*self.library.diagnostics]
        circuit: Circuit | None = None
        layout: Layout | None = None
        routes: list[RoutedNet] = []
        scene: SchematicScene | None = None

        source_path = ROOT / case.path
        try:
            circuit, parse_diagnostics = self.parser.parse_file(source_path)
            diagnostics.extend(parse_diagnostics)
            result.stages["parse"] = "pass" if circuit else "fail"
        except Exception as exc:
            diagnostics.append(_exception_diagnostic("PARSE_EXCEPTION", f"Parse crashed for {case.id}", exc))
            result.stages["parse"] = "fail"

        if circuit is not None:
            validation = _run_stage("validation", result, diagnostics, lambda: CircuitValidator(self.library).validate(circuit))
            validation_blocked = has_blocking_diagnostics(validation)
            pipeline_allowed = not validation_blocked or (case.classification == "negative" and case.render_allowed)
            if pipeline_allowed:
                _run_stage("topology", result, diagnostics, lambda: TopologyAnalyzer().analyze(circuit, self.library))
                layout = self._place(circuit, result, diagnostics, source_path)
                if layout is not None:
                    routes = self._route(circuit, layout, result, diagnostics)
                    scene = self._build_scene(circuit, layout, routes, result, diagnostics)
                    if scene is not None:
                        _run_stage("drc", result, diagnostics, lambda: visual_drc_from_scene(scene)[0])
                        _run_rendered_connectivity_stage(circuit, scene, result, diagnostics)
                    if can_run_electrical_rules(validation):
                        _run_stage("erc", result, diagnostics, lambda: run_electrical_rules(circuit, self.library))
                        if validation_blocked:
                            result.stages["erc"] = "partial_expected"
                    else:
                        result.stages["erc"] = "blocked_expected" if validation_blocked else "blocked"
            else:
                result.stages["topology"] = "blocked"
                result.stages["placement"] = "blocked"
                result.stages["routing"] = "blocked"
                result.stages["scene"] = "blocked"
                result.stages["drc"] = "blocked"
                result.stages["rendered_connectivity"] = "blocked"
                if can_run_electrical_rules(validation):
                    _run_stage("erc", result, diagnostics, lambda: run_electrical_rules(circuit, self.library))
                    result.stages["erc"] = "partial_expected"
                else:
                    result.stages["erc"] = "blocked"

        if scene is not None and case.render_allowed:
            self._export(scene, result, diagnostics)
        elif not case.render_allowed:
            result.stages["svg_export"] = "blocked_expected"
            result.stages["png_export"] = "blocked_expected"
        else:
            result.stages.setdefault("svg_export", "blocked")
            result.stages.setdefault("png_export", "blocked")

        result.elapsed_ms = round((perf_counter() - started) * 1000, 3)
        return self._finalize(case, result, diagnostics, scene, routes)

    def _place(self, circuit: Circuit, result: AuditResult, diagnostics: list[Diagnostic], source_path: Path) -> Layout | None:
        try:
            layout = DeterministicPlacementEngine().place(circuit, self.library, _layout_for_source(source_path, diagnostics))
            missing = sorted(component.ref for component in circuit.components if component.ref not in layout.components)
            if missing:
                diagnostics.append(
                    Diagnostic(
                        severity=Severity.ERROR,
                        code="PLACEMENT_MISSING_COMPONENT",
                        message=f"Placement missing components: {', '.join(missing)}",
                        metadata={"refs": missing},
                    )
                )
            _placement_metadata_diagnostics(layout, diagnostics)
            result.stages["placement"] = "fail" if missing else "pass"
            return layout
        except Exception as exc:
            diagnostics.append(_exception_diagnostic("PLACEMENT_EXCEPTION", "Placement crashed", exc))
            result.stages["placement"] = "fail"
            return None

    def _route(self, circuit: Circuit, layout: Layout, result: AuditResult, diagnostics: list[Diagnostic]) -> list[RoutedNet]:
        try:
            router = ManhattanRouter.for_audit() if self.validate_route_styles else ManhattanRouter.for_interactive()
            routes = router.route(circuit, self.library, layout)
            routing_diagnostics = _routing_diagnostics(routes)
            diagnostics.extend(routing_diagnostics)
            result.stages["routing"] = "fail" if routing_diagnostics else "pass"
            return routes
        except Exception as exc:
            diagnostics.append(_exception_diagnostic("ROUTING_EXCEPTION", "Routing crashed", exc))
            result.stages["routing"] = "fail"
            return []

    def _build_scene(self, circuit: Circuit, layout: Layout, routes: list[RoutedNet], result: AuditResult, diagnostics: list[Diagnostic]) -> SchematicScene | None:
        try:
            scene = build_schematic_scene(circuit, self.library, layout, routes, quality=RenderQualityMode.AUDIT)
            scene_diagnostics = _scene_diagnostics(scene)
            diagnostics.extend(scene_diagnostics)
            result.stages["scene"] = "fail" if scene_diagnostics else "pass"
            return scene
        except Exception as exc:
            diagnostics.append(_exception_diagnostic("SCENE_EXCEPTION", "Scene build crashed", exc))
            result.stages["scene"] = "fail"
            return None

    def _export(self, scene: SchematicScene, result: AuditResult, diagnostics: list[Diagnostic]) -> None:
        svg_path = self.output_root / "svg" / f"{case_safe_id(result.case_id)}.svg"
        png_path = self.output_root / "png" / f"{case_safe_id(result.case_id)}.png"
        try:
            export_svg_from_scene(scene, svg_path)
            if not svg_path.exists() or not svg_path.read_text(encoding="utf-8").lstrip().startswith("<svg"):
                diagnostics.append(Diagnostic(severity=Severity.ERROR, code="EXPORT_SVG_INVALID", message="SVG export did not produce an SVG document"))
                result.stages["svg_export"] = "fail"
            else:
                result.svg_path = _display_path(svg_path)
                result.stages["svg_export"] = "pass"
        except Exception as exc:
            diagnostics.append(_exception_diagnostic("EXPORT_SVG_FAILED", "SVG export failed", exc))
            result.stages["svg_export"] = "fail"
        try:
            export_png_from_scene(scene, png_path)
            data = png_path.read_bytes() if png_path.exists() else b""
            if not data.startswith(b"\x89PNG"):
                diagnostics.append(Diagnostic(severity=Severity.ERROR, code="EXPORT_PNG_INVALID", message="PNG export did not produce valid PNG bytes"))
                result.stages["png_export"] = "fail"
            else:
                result.png_path = _display_path(png_path)
                result.stages["png_export"] = "pass"
        except RuntimeError as exc:
            result.stages["png_export"] = "unavailable"
            result.metrics["png_unavailable"] = str(exc)
        except Exception as exc:
            diagnostics.append(_exception_diagnostic("EXPORT_PNG_FAILED", "PNG export failed", exc))
            result.stages["png_export"] = "fail"

    def _finalize(
        self,
        case: AuditCase,
        result: AuditResult,
        diagnostics: list[Diagnostic],
        scene: SchematicScene | None,
        routes: list[RoutedNet],
    ) -> AuditResult:
        normalized = normalize_diagnostics(diagnostics)
        result.diagnostics = [asdict(item) for item in normalized]
        result.actual_codes = diagnostic_codes(normalized)
        comparison = compare_expected_diagnostics(list(case.expected_diagnostics), result.actual_codes, list(case.allowed_diagnostics))
        result.missing_expected_codes = comparison.missing_expected_codes
        result.unexpected_codes = comparison.unexpected_codes
        result.expected_match = comparison.matched
        result.metrics.update(_case_metrics(diagnostics, scene, routes))
        required_stages = _required_stages(case)
        stages_ok = all(result.stages.get(stage) in {"pass", "unavailable", "blocked_expected", "partial_expected"} for stage in required_stages)
        if case.classification == "clean":
            stages_ok = stages_ok and result.stages.get("png_export") in {"pass", "unavailable"}
        result.overall = "pass" if result.expected_match and stages_ok else "fail"
        return result

    def _prepare_output_dirs(self) -> None:
        for name in ("svg", "png"):
            (self.output_root / name).mkdir(parents=True, exist_ok=True)

    def write_reports(self, results: list[AuditResult]) -> tuple[Path, Path]:
        self.output_root.mkdir(parents=True, exist_ok=True)
        report_json = self.output_root / "report.json"
        report_md = self.output_root / "report.md"
        payload = {"summary": audit_summary(results), "cases": [asdict(result) for result in results]}
        report_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        report_md.write_text(markdown_report(results), encoding="utf-8")
        return report_json, report_md


def _run_stage(stage: str, result: AuditResult, diagnostics: list[Diagnostic], callback: Any) -> Any:
    try:
        stage_result = callback()
        if isinstance(stage_result, list):
            diagnostics.extend(stage_result)
            result.stages[stage] = "pass"
        else:
            result.stages[stage] = "pass"
        return stage_result
    except Exception as exc:
        diagnostics.append(_exception_diagnostic(f"{stage.upper()}_EXCEPTION", f"{stage.title()} crashed", exc))
        result.stages[stage] = "fail"
        return []


def _run_rendered_connectivity_stage(circuit: Circuit, scene: SchematicScene, result: AuditResult, diagnostics: list[Diagnostic]) -> None:
    try:
        connectivity_diagnostics, connectivity_metrics = validate_rendered_connectivity(circuit, scene)
        diagnostics.extend(connectivity_diagnostics)
        result.metrics["rendered_connectivity"] = connectivity_metrics
        result.stages["rendered_connectivity"] = "pass" if not any(diag.severity in {Severity.ERROR, Severity.FATAL} for diag in connectivity_diagnostics) else "fail"
    except Exception as exc:
        diagnostics.append(_exception_diagnostic("RENDERED_CONNECTIVITY_EXCEPTION", "Rendered connectivity crashed", exc))
        result.stages["rendered_connectivity"] = "fail"


def _layout_for_source(source_path: Path, diagnostics: list[Diagnostic]) -> Layout | None:
    layout_path = source_path.with_suffix(".layout.json")
    if not layout_path.exists():
        return None
    try:
        return Layout.model_validate_json(layout_path.read_text(encoding="utf-8"))
    except Exception as exc:
        diagnostics.append(_exception_diagnostic("PLACEMENT_LAYOUT_INVALID", f"Saved layout is invalid for {source_path}", exc))
        return None


def _placement_metadata_diagnostics(layout: Layout, diagnostics: list[Diagnostic]) -> None:
    optimizer = layout.canvas.get("placement_optimizer", {})
    optimized = optimizer.get("optimized", {}) if isinstance(optimizer, dict) else {}
    hard_count = int(optimized.get("hard_violation_count", 0)) if isinstance(optimized, dict) else 0
    if hard_count:
        diagnostics.append(
            Diagnostic(
                severity=Severity.ERROR,
                code="PLACEMENT_HARD_VIOLATION",
                message=f"Placement optimizer reported {hard_count} hard violations",
                metadata={"placement_optimizer": optimizer},
            )
        )


def _routing_diagnostics(routes: list[RoutedNet]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for route in routes:
        for warning in route.warnings:
            diagnostics.append(Diagnostic(severity=Severity.WARNING, code=_routing_warning_code(warning), message=warning, net_name=route.name))
    return diagnostics


def _routing_warning_code(warning: str) -> str:
    if warning.startswith("Cannot route"):
        return "ROUTING_UNROUTABLE_PIN"
    if warning.startswith("Could not find"):
        return "ROUTING_FAILED"
    if "only one routable pin" in warning:
        return "ROUTING_SINGLE_PIN_NET"
    return "ROUTING_WARNING"


def _scene_diagnostics(scene: SchematicScene) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if not scene.elements:
        diagnostics.append(Diagnostic(severity=Severity.ERROR, code="SCENE_EMPTY", message="Scene contains no elements"))
    ids = [element.id for element in scene.elements]
    duplicate_ids = sorted(item for item, count in Counter(ids).items() if count > 1)
    if duplicate_ids:
        diagnostics.append(Diagnostic(severity=Severity.ERROR, code="SCENE_DUPLICATE_ID", message=f"Duplicate scene IDs: {', '.join(duplicate_ids)}", metadata={"ids": duplicate_ids}))
    if not _bounds_finite(scene.canvas_bounds):
        diagnostics.append(Diagnostic(severity=Severity.ERROR, code="SCENE_NONFINITE_CANVAS_BOUNDS", message="Scene canvas bounds are not finite"))
    bad_bounds = sorted(element.id for element in scene.elements if not _bounds_finite(element.bounds))
    if bad_bounds:
        diagnostics.append(Diagnostic(severity=Severity.ERROR, code="SCENE_NONFINITE_ELEMENT_BOUNDS", message=f"Non-finite element bounds: {', '.join(bad_bounds)}", metadata={"ids": bad_bounds}))
    return diagnostics


def _bounds_finite(bounds: Any) -> bool:
    return all(math.isfinite(float(value)) for value in (bounds.min_x, bounds.min_y, bounds.max_x, bounds.max_y))


def _exception_diagnostic(code: str, message: str, exc: Exception) -> Diagnostic:
    return Diagnostic(severity=Severity.ERROR, code=code, message=f"{message}: {exc}", metadata={"exception_type": type(exc).__name__})


def _display_path(path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def _required_stages(case: AuditCase) -> list[str]:
    if not case.render_allowed:
        return ["parse", "validation"]
    return ["parse", "validation", "topology", "placement", "routing", "scene", "drc", "rendered_connectivity", "erc", "svg_export", "png_export"]


def _case_metrics(diagnostics: list[Diagnostic], scene: SchematicScene | None, routes: list[RoutedNet]) -> dict[str, Any]:
    codes = diagnostic_codes(diagnostics)
    by_subsystem = Counter(diagnostic_subsystem(code) for code in codes)
    route_lengths = [sum(abs(x1 - x2) + abs(y1 - y2) for x1, y1, x2, y2 in route.segments) for route in routes]
    return {
        "diagnostic_count": len(codes),
        "diagnostics_by_subsystem": dict(sorted(by_subsystem.items())),
        "scene_elements": len(scene.elements) if scene else 0,
        "scene_width": scene.canvas_bounds.width if scene else 0,
        "scene_height": scene.canvas_bounds.height if scene else 0,
        "route_count": len(routes),
        "total_wire_length": sum(route_lengths),
        "max_net_length": max(route_lengths) if route_lengths else 0,
    }


def audit_summary(results: list[AuditResult]) -> dict[str, Any]:
    clean = [result for result in results if result.classification == "clean"]
    negative = [result for result in results if result.classification == "negative"]
    diagnostics_by_subsystem: Counter[str] = Counter()
    for result in results:
        diagnostics_by_subsystem.update(result.metrics.get("diagnostics_by_subsystem", {}))
    slowest = sorted(results, key=lambda result: result.elapsed_ms, reverse=True)[:5]
    return {
        "total_circuits": len(results),
        "clean_circuits": len(clean),
        "negative_circuits": len(negative),
        "passed_clean_circuits": sum(1 for result in clean if result.overall == "pass"),
        "failed_clean_circuits": sum(1 for result in clean if result.overall != "pass"),
        "passed_negative_circuits": sum(1 for result in negative if result.overall == "pass"),
        "failed_negative_circuits": sum(1 for result in negative if result.overall != "pass"),
        "unexpected_diagnostics": sum(len(result.unexpected_codes) for result in results),
        "missing_expected_diagnostics": sum(len(result.missing_expected_codes) for result in results),
        "diagnostics_by_subsystem": dict(sorted(diagnostics_by_subsystem.items())),
        "svg_exports": sum(1 for result in results if result.svg_path),
        "png_exports": sum(1 for result in results if result.png_path),
        "slowest_circuits": [{"id": result.case_id, "elapsed_ms": result.elapsed_ms} for result in slowest],
    }


def markdown_report(results: list[AuditResult]) -> str:
    summary = audit_summary(results)
    rows = [
        "| Circuit | Class | Family | Result | Diagnostics | SVG | PNG | Runtime ms |",
        "| --- | --- | --- | --- | ---: | --- | --- | ---: |",
    ]
    for result in results:
        rows.append(
            f"| `{result.case_id}` | {result.classification} | {result.topology_family} | {result.overall} | "
            f"{len(result.actual_codes)} | {result.stages.get('svg_export', 'skip')} | {result.stages.get('png_export', 'skip')} | {result.elapsed_ms:.1f} |"
        )
    slowest = ", ".join(f"{item['id']} ({item['elapsed_ms']:.1f} ms)" for item in summary["slowest_circuits"]) or "none"
    return "\n".join(
        [
            "# Circuit Audit Report",
            "",
            f"Total circuits: {summary['total_circuits']}",
            f"Clean circuits: {summary['passed_clean_circuits']}/{summary['clean_circuits']} passed",
            f"Negative circuits: {summary['passed_negative_circuits']}/{summary['negative_circuits']} passed",
            f"Total unexpected diagnostics: {summary['unexpected_diagnostics']}",
            f"Missing expected diagnostics: {summary['missing_expected_diagnostics']}",
            f"SVG exports: {summary['svg_exports']}",
            f"PNG exports: {summary['png_exports']}",
            f"Slowest circuits: {slowest}",
            "",
            *rows,
            "",
        ]
    )


def case_safe_id(case_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in case_id)


def filter_cases(cases: list[AuditCase], args: argparse.Namespace) -> list[AuditCase]:
    if args.clean_only:
        cases = [case for case in cases if case.classification == "clean"]
    if args.negative_only:
        cases = [case for case in cases if case.classification == "negative"]
    if args.case:
        needle = args.case.lower()
        cases = [case for case in cases if needle in case.id.lower() or needle in case.path.lower() or needle in case.topology_family.lower()]
    return cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the zero-diagnostic circuit quality gate.")
    parser.add_argument("--all", action="store_true", help="Run all clean and negative manifest circuits.")
    parser.add_argument("--clean-only", action="store_true", help="Run only clean circuits.")
    parser.add_argument("--negative-only", action="store_true", help="Run only negative circuits.")
    parser.add_argument("--case", help="Run one case id/path/family substring.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT, help="Audit output directory.")
    args = parser.parse_args(argv)
    if not args.all and not args.clean_only and not args.negative_only and not args.case:
        args.all = True
    cases = filter_cases(load_audit_cases(), args)
    runner = CircuitAuditRunner(args.output)
    results = runner.run(cases)
    report_json, report_md = runner.write_reports(results)
    summary = audit_summary(results)
    print(
        "Circuit audit complete: "
        f"clean {summary['passed_clean_circuits']}/{summary['clean_circuits']} passed, "
        f"negative {summary['passed_negative_circuits']}/{summary['negative_circuits']} passed, "
        f"unexpected diagnostics {summary['unexpected_diagnostics']}"
    )
    print(f"JSON report: {report_json}")
    print(f"Markdown report: {report_md}")
    failures = [result for result in results if result.overall != "pass"]
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
