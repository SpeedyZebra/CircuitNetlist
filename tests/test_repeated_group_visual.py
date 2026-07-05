from __future__ import annotations

from pathlib import Path

import pytest

from circuit_netlist.component_library import load_component_library
from circuit_netlist.models import Layout, Placement
from circuit_netlist.parser import NetlistParser
from circuit_netlist.placement import DeterministicPlacementEngine
from circuit_netlist.regression import visual_drc
from circuit_netlist.renderer import render_circuit
from circuit_netlist.router import ManhattanRouter
from circuit_netlist.topology import PatternType, TopologyAnalyzer
from circuit_netlist.validator import CircuitValidator, has_blocking_diagnostics


ROOT = Path(__file__).resolve().parents[1]
STRESS_ROOT = ROOT / "test_circuits" / "good" / "04_repeated_groups"
pytestmark = [pytest.mark.integration, pytest.mark.slow]


def stable_layout_dump(layout: Layout) -> dict[str, object]:
    data = layout.model_dump(mode="json")
    canvas = dict(data["canvas"])
    optimizer = canvas.get("placement_optimizer")
    if isinstance(optimizer, dict):
        canvas["placement_optimizer"] = {
            "mode": optimizer.get("mode"),
            "optimized": optimizer.get("optimized"),
            "fast_path": optimizer.get("fast_path"),
        }
    return {**data, "canvas": canvas}


def run_pipeline(path: Path, existing: Layout | None = None):
    library = load_component_library(ROOT / "components")
    circuit, parse_diagnostics = NetlistParser().parse_file(path)
    assert circuit is not None, parse_diagnostics
    validation = CircuitValidator(library).validate(circuit)
    assert not has_blocking_diagnostics(validation), validation
    analysis = TopologyAnalyzer().analyze(circuit, library)
    layout = DeterministicPlacementEngine().place(circuit, library, existing)
    layout_again = DeterministicPlacementEngine().place(circuit, library, existing)
    assert stable_layout_dump(layout) == stable_layout_dump(layout_again)
    routes = ManhattanRouter(validate_auto_route_styles=False).route(circuit, library, layout)
    drc, metrics = visual_drc(circuit, library, layout, routes)
    render_circuit(circuit, library, layout, routes, [*validation, *drc])
    return circuit, analysis, layout, drc, metrics


def assert_visual_clean(path: Path):
    circuit, analysis, layout, drc, metrics = run_pipeline(path)
    assert [diag.code for diag in drc] == []
    assert metrics["component_body_overlaps"] == 0
    assert metrics["wire_symbol_overlaps"] == 0
    assert metrics["unrelated_wire_overlaps"] == 0
    assert metrics["physical_wire_text_overlaps"] == 0
    auto_centers = [(placement.x, placement.y) for placement in layout.components.values() if not placement.locked]
    assert len(auto_centers) == len(set(auto_centers))
    return circuit, analysis, layout


def test_two_repeated_mosfet_channels_full_visual_pipeline() -> None:
    _, analysis, layout = assert_visual_clean(STRESS_ROOT / "multi_mosfet_2_channel.cnet")
    assert len(analysis.patterns_by_type(PatternType.LOW_SIDE_MOSFET_SWITCH)) == 2
    assert layout.components["R81"].y == layout.components["Q12"].y
    assert layout.components["R27"].y > layout.components["R81"].y
    assert layout.components["D14"].y < layout.components["Q12"].y
    assert layout.components["Q12"].y != layout.components["Q47"].y


def test_four_repeated_mosfet_channels_full_visual_pipeline() -> None:
    circuit, analysis, layout = assert_visual_clean(STRESS_ROOT / "multi_mosfet_4_channel.cnet")
    assert len(analysis.patterns_by_type(PatternType.LOW_SIDE_MOSFET_SWITCH)) == 4
    mosfet_rows = [layout.components[ref].y for ref in ["Q12", "Q47", "Q5", "Q88"]]
    assert len(mosfet_rows) == len(set(mosfet_rows))
    library = load_component_library(ROOT / "components")
    assert stable_layout_dump(layout) == stable_layout_dump(DeterministicPlacementEngine().place(circuit, library))


def test_multiple_voltage_dividers_full_visual_pipeline() -> None:
    _, analysis, layout = assert_visual_clean(STRESS_ROOT / "multi_voltage_divider.cnet")
    assert len(analysis.patterns_by_type(PatternType.VOLTAGE_DIVIDER)) == 4
    columns = {layout.components[pattern.metadata["top_resistor"]].x for pattern in analysis.patterns_by_type(PatternType.VOLTAGE_DIVIDER)}
    assert len(columns) == 4


def test_multiple_rc_filters_full_visual_pipeline() -> None:
    _, analysis, layout = assert_visual_clean(STRESS_ROOT / "multi_rc_filter.cnet")
    assert len(analysis.patterns_by_type(PatternType.RC_LOWPASS)) == 2
    assert layout.components["R3"].y != layout.components["R81"].y
    assert layout.components["C29"].y != layout.components["C41"].y


def test_multiple_decoupling_capacitors_full_visual_pipeline() -> None:
    _, analysis, layout = assert_visual_clean(STRESS_ROOT / "multi_decoupling.cnet")
    assert len(analysis.patterns_by_type(PatternType.DECOUPLING_CAPACITOR)) == 2
    assert len({(layout.components[ref].x, layout.components[ref].y) for ref in ["C29", "C41", "C7", "C63"]}) == 4
    assert layout.components["C7"].y > layout.components["C29"].y


def test_multiple_decoupling_with_rc_filters_full_visual_pipeline() -> None:
    _, analysis, layout = assert_visual_clean(STRESS_ROOT / "multi_decoupling_with_rc_filters.cnet")
    assert len(analysis.patterns_by_type(PatternType.DECOUPLING_CAPACITOR)) == 2
    assert len(analysis.patterns_by_type(PatternType.RC_LOWPASS)) == 2
    assert layout.components["C7"].x != layout.components["C54"].x
    assert layout.components["C63"].x != layout.components["C12"].x


def test_locked_component_remains_fixed_near_repeated_group_lane() -> None:
    existing = Layout(components={"Q47": Placement(x=2222, y=1888, rotation=90, locked=True)})
    _, _, layout, drc, metrics = run_pipeline(STRESS_ROOT / "multi_mosfet_2_channel.cnet", existing)
    assert layout.components["Q47"] == Placement(x=2222, y=1888, rotation=90, locked=True)
    assert metrics["component_body_overlaps"] == 0
    assert not [diag for diag in drc if diag.code == "DRC_COMPONENT_OVERLAP"]
