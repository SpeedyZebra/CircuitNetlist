from __future__ import annotations

import argparse
import json
from pathlib import Path

from .component_library import load_component_library
from .parser import NetlistParser
from .topology import TopologyAnalyzer
from .validator import CircuitValidator, has_blocking_diagnostics


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description="Print topology analysis for a .cnet file as JSON.")
    parser.add_argument("netlist", type=Path)
    args = parser.parse_args()

    netlist_path = args.netlist if args.netlist.is_absolute() else ROOT / args.netlist
    library = load_component_library(ROOT / "components")
    circuit, diagnostics = NetlistParser().parse_file(netlist_path)
    diagnostics.extend(library.diagnostics)
    if circuit is not None:
        diagnostics.extend(CircuitValidator(library).validate(circuit))
    if circuit is None or has_blocking_diagnostics(diagnostics):
        print(json.dumps({"diagnostics": [diag.model_dump(mode="json") for diag in diagnostics], "topology": None}, indent=2))
        return 1
    analysis = TopologyAnalyzer().analyze(circuit, library)
    print(json.dumps({"diagnostics": [diag.model_dump(mode="json") for diag in diagnostics], "topology": analysis.model_dump(mode="json")}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
