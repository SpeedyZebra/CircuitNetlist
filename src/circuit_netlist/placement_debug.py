from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .component_library import load_component_library
from .constraint_placement import ConstraintPlacementOptimizer, PlacementOptimizationConfig
from .erc import ElectricalRuleChecker
from .models import Diagnostic, Layout, Severity
from .parser import NetlistParser
from .placement import DeterministicPlacementEngine
from .router import ManhattanRouter
from .scene_builder import build_schematic_scene
from .scene_renderer import render_scene_svg
from .topology import TopologyAnalyzer
from .validator import CircuitValidator, has_blocking_diagnostics


ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    netlist_path = Path(args.netlist)
    component_root = Path(args.components)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    library = load_component_library(component_root)
    circuit, diagnostics = NetlistParser().parse_file(netlist_path)
    if circuit is None:
        _write_json(output_dir / "diagnostics.json", [diag.model_dump(mode="json") for diag in diagnostics])
        print(f"Could not parse {netlist_path}")
        return 1

    diagnostics.extend(library.diagnostics)
    diagnostics.extend(CircuitValidator(library).validate(circuit))
    if has_blocking_diagnostics(diagnostics):
        _write_json(output_dir / "diagnostics.json", [diag.model_dump(mode="json") for diag in diagnostics])
        print(f"Validation blocked placement debug output for {netlist_path}")
        return 1

    existing_layout = _read_layout(args.layout)
    fixed_refs = set(existing_layout.components) if existing_layout else set()
    analysis = TopologyAnalyzer().analyze(circuit, library)
    initial_layout = DeterministicPlacementEngine(
        optimization_config=PlacementOptimizationConfig(mode="off", grid=args.grid)
    ).place(circuit, library, existing_layout)
    config = PlacementOptimizationConfig(
        mode=args.mode,
        grid=args.grid,
        max_passes=args.max_passes,
        max_total_evaluations=args.max_evaluations,
        optimize_soft_constraints=args.optimize_soft,
    )
    result = ConstraintPlacementOptimizer(config).optimize(circuit, library, initial_layout, analysis, fixed_refs=fixed_refs)
    optimized_layout = result.optimized_layout
    optimized_layout.canvas["placement_optimizer"] = result.comparison.to_dict()

    diagnostics.extend(ElectricalRuleChecker(library).check(circuit))
    routes = ManhattanRouter().route(circuit, library, optimized_layout)
    for route in routes:
        for warning in route.warnings:
            diagnostics.append(Diagnostic(severity=Severity.WARNING, message=warning))
    scene = build_schematic_scene(circuit, library, optimized_layout, routes)
    svg = render_scene_svg(scene, diagnostics)

    _write_json(output_dir / "placement_initial.json", initial_layout.model_dump(mode="json"))
    _write_json(output_dir / "placement_optimized.json", optimized_layout.model_dump(mode="json"))
    _write_json(output_dir / "placement_score.json", result.comparison.to_dict())
    _write_json(output_dir / "topology.json", analysis.model_dump(mode="json"))
    (output_dir / "optimized.svg").write_text(svg, encoding="utf-8")
    (output_dir / "placement_comparison.txt").write_text(_comparison_text(result.comparison.to_dict()), encoding="utf-8")

    print(f"Placement debug written to {output_dir}")
    print(f"Initial score: {result.comparison.initial_score.total:.2f}")
    print(f"Optimized score: {result.comparison.optimized_score.total:.2f}")
    print(f"Hard violations: {result.comparison.initial_score.hard_violation_count} -> {result.comparison.optimized_score.hard_violation_count}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write before/after diagnostics for constraint-scored placement.")
    parser.add_argument("netlist", help="Path to a .cnet netlist.")
    parser.add_argument("--layout", help="Optional existing layout JSON. Components in this layout are treated as fixed/manual.")
    parser.add_argument("--components", default=str(ROOT / "components"), help="Component library root.")
    parser.add_argument("--output", default=str(ROOT / "output" / "placement_debug"), help="Output directory for score, layout, and SVG artifacts.")
    parser.add_argument("--mode", choices=["off", "score_only", "optimize"], default="optimize")
    parser.add_argument("--grid", type=int, default=40)
    parser.add_argument("--max-passes", type=int, default=4)
    parser.add_argument("--max-evaluations", type=int, default=600)
    parser.add_argument("--optimize-soft", action="store_true", help="Allow soft-constraint improvements after hard violations are resolved.")
    return parser.parse_args(argv)


def _read_layout(path_text: str | None) -> Layout | None:
    if not path_text:
        return None
    path = Path(path_text)
    return Layout.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _comparison_text(comparison: dict[str, object]) -> str:
    initial = comparison["initial_score"]
    optimized = comparison["optimized_score"]
    assert isinstance(initial, dict)
    assert isinstance(optimized, dict)
    return "\n".join(
        [
            "Constraint-Scored Placement Comparison",
            "",
            f"Initial total: {initial['total']}",
            f"Optimized total: {optimized['total']}",
            f"Hard violations: {initial['hard_violation_count']} -> {optimized['hard_violation_count']}",
            f"Passes: {comparison['passes']}",
            f"Candidate evaluations: {comparison['candidate_evaluations']}",
            f"Budget reached: {comparison['budget_reached']}",
            "",
            "Moves:",
            json.dumps(comparison["moves"], indent=2),
        ]
    )


if __name__ == "__main__":
    sys.exit(main())
