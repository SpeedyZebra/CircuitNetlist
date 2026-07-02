from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .component_library import load_component_library
from .erc import ElectricalRuleChecker
from .exporters import export_svg_from_scene
from .models import Diagnostic, Layout, Severity
from .parser import NetlistParser
from .placement import DeterministicPlacementEngine
from .router import ManhattanRouter
from .scene_builder import build_schematic_scene
from .validator import CircuitValidator, has_blocking_diagnostics


ROOT = Path(__file__).resolve().parents[2]
COMPONENT_ROOT = ROOT / "components"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dump canonical schematic scene geometry.")
    parser.add_argument("netlist", nargs="?", default=str(ROOT / "examples" / "solar_led.cnet"))
    parser.add_argument("--layout", help="Optional layout JSON file.")
    parser.add_argument("--json", dest="json_path", help="Write scene JSON to this path.")
    parser.add_argument("--svg", dest="svg_path", help="Write scene-derived SVG to this path.")
    args = parser.parse_args(argv)

    netlist_path = Path(args.netlist)
    library = load_component_library(COMPONENT_ROOT)
    circuit, diagnostics = NetlistParser().parse_file(netlist_path)
    if circuit is None:
        _print_diagnostics(diagnostics)
        return 1
    diagnostics.extend(library.diagnostics)
    diagnostics.extend(CircuitValidator(library).validate(circuit))
    if has_blocking_diagnostics(diagnostics):
        _print_diagnostics(diagnostics)
        return 1
    diagnostics.extend(ElectricalRuleChecker(library).check(circuit))
    existing_layout = _read_layout(args.layout)
    layout = DeterministicPlacementEngine().place(circuit, library, existing_layout)
    routes = ManhattanRouter().route(circuit, library, layout)
    for route in routes:
        for warning in route.warnings:
            diagnostics.append(Diagnostic(severity=Severity.WARNING, message=warning))
    scene = build_schematic_scene(circuit, library, layout, routes)
    if args.json_path:
        json_path = Path(args.json_path)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(scene.model_dump(mode="json"), indent=2), encoding="utf-8")
    if args.svg_path:
        export_svg_from_scene(scene, Path(args.svg_path), diagnostics)
    print(
        json.dumps(
            {
                "circuit": circuit.name,
                "scene_version": scene.scene_version,
                "elements": len(scene.elements),
                "canvas": scene.canvas_bounds.model_dump(mode="json"),
                "routes": len(routes),
                "diagnostics": [diag.model_dump(mode="json") for diag in diagnostics],
            },
            indent=2,
        )
    )
    return 0


def _read_layout(path: str | None) -> Layout | None:
    if not path:
        return None
    layout_path = Path(path)
    if not layout_path.exists():
        return None
    return Layout.model_validate_json(layout_path.read_text(encoding="utf-8"))


def _print_diagnostics(diagnostics: list[Diagnostic]) -> None:
    for diag in diagnostics:
        print(f"{diag.severity.value}: {diag.message}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
