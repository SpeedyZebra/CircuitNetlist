from __future__ import annotations

from pathlib import Path

from circuit_netlist.component_library import load_component_library
from circuit_netlist.constraint_placement import ConstraintPlacementOptimizer, PlacementOptimizationConfig, PlacementScorer
from circuit_netlist.models import Circuit, ComponentInstance, Layout, Net, PinRef, Placement
from circuit_netlist.parser import NetlistParser
from circuit_netlist.placement import DeterministicPlacementEngine
from circuit_netlist.regression import visual_drc
from circuit_netlist.router import ManhattanRouter
from circuit_netlist.topology import PatternType, TopologyAnalyzer


ROOT = Path(__file__).resolve().parents[1]


def library():
    return load_component_library(ROOT / "components")


def two_resistors() -> Circuit:
    return Circuit(
        name="TwoResistors",
        components=[
            ComponentInstance(ref="R1", component_id="BASIC_RESISTOR"),
            ComponentInstance(ref="R2", component_id="BASIC_RESISTOR"),
        ],
        nets=[Net(name="N", pins=[PinRef(component_ref="R1", pin_name="2"), PinRef(component_ref="R2", pin_name="1")])],
    )


def layout_at(points: dict[str, tuple[int, int]], *, locked: set[str] | None = None) -> Layout:
    locked = locked or set()
    return Layout(
        components={ref: Placement(x=x, y=y, locked=ref in locked) for ref, (x, y) in points.items()},
        canvas={"grid": 40, "width": 1400, "height": 1000},
    )


def low_side_text() -> str:
    return """CIRCUIT LowSide
COMPONENT V1 POWER_DC_SOURCE voltage=5V role=source
COMPONENT U1 MCU_ATtiny402_SOIC8 role=controller
COMPONENT R_LED BASIC_RESISTOR value=220ohm role=current_limit
COMPONENT LED1 LIGHT_LED_RED
COMPONENT R_GATE BASIC_RESISTOR value=100ohm role=gate_resistor
COMPONENT R_PULL BASIC_RESISTOR value=100kohm role=pulldown
COMPONENT Q1 BASIC_NMOS role=low_side_switch

NET VCC:
    V1.POS
    U1.VDD
    R_LED.1

NET LED_ANODE:
    R_LED.2
    LED1.A

NET LED_SWITCH:
    LED1.K
    Q1.D

NET CTRL:
    U1.PA7
    R_GATE.1

NET GATE:
    R_GATE.2
    R_PULL.1
    Q1.G

NET GND:
    V1.NEG
    U1.GND
    R_PULL.2
    Q1.S
"""


def parse_text(text: str):
    circuit, diagnostics = NetlistParser().parse_text(text)
    assert circuit is not None, diagnostics
    return circuit


def test_component_overlap_is_a_hard_violation_and_scores_worse_than_spacing() -> None:
    lib = library()
    circuit = two_resistors()
    scorer = PlacementScorer()

    overlapping = scorer.score(circuit, lib, layout_at({"R1": (100, 100), "R2": (100, 100)}))
    separated = scorer.score(circuit, lib, layout_at({"R1": (100, 100), "R2": (340, 100)}))

    assert overlapping.hard_violation_count > separated.hard_violation_count
    assert overlapping.hard_penalty >= 1_000_000
    assert overlapping.total > separated.total
    assert any(violation.code == "NO_COMPONENT_BODY_OVERLAP" for violation in overlapping.violations)


def test_wire_length_bend_count_page_area_and_aspect_ratio_affect_score() -> None:
    lib = library()
    circuit = two_resistors()
    scorer = PlacementScorer()

    horizontal = scorer.score(circuit, lib, layout_at({"R1": (100, 100), "R2": (360, 100)}))
    balanced = scorer.score(circuit, lib, layout_at({"R1": (100, 100), "R2": (260, 220)}))
    diagonal_far = scorer.score(circuit, lib, layout_at({"R1": (100, 100), "R2": (1160, 820)}))
    tall = scorer.score(circuit, lib, layout_at({"R1": (100, 100), "R2": (100, 860)}))

    assert horizontal.estimated_wire_length_penalty < diagonal_far.estimated_wire_length_penalty
    assert horizontal.estimated_bend_penalty < diagonal_far.estimated_bend_penalty
    assert horizontal.page_area_penalty < diagonal_far.page_area_penalty
    assert balanced.aspect_ratio_penalty == 0
    assert tall.aspect_ratio_penalty > balanced.aspect_ratio_penalty
    assert horizontal.aspect_ratio_penalty > balanced.aspect_ratio_penalty


def test_controller_closer_to_driven_group_scores_better() -> None:
    lib = library()
    circuit = parse_text(low_side_text())
    analysis = TopologyAnalyzer().analyze(circuit, lib)
    scorer = PlacementScorer()
    common = {
        "V1": (80, 120),
        "R_LED": (620, 160),
        "LED1": (880, 160),
        "R_GATE": (620, 500),
        "R_PULL": (820, 720),
        "Q1": (880, 500),
    }

    near = scorer.score(circuit, lib, layout_at({**common, "U1": (360, 460)}), analysis)
    far = scorer.score(circuit, lib, layout_at({**common, "U1": (80, 900)}), analysis)

    assert near.metrics["controller_distance_penalty"] < far.metrics["controller_distance_penalty"]
    assert near.total < far.total


def test_optimizer_moves_automatic_component_away_from_locked_obstacle() -> None:
    lib = library()
    circuit = two_resistors()
    initial = layout_at({"R1": (100, 100), "R2": (100, 100)}, locked={"R1"})
    result = ConstraintPlacementOptimizer().optimize(circuit, lib, initial)

    assert result.optimized_layout.components["R1"] == Placement(x=100, y=100, locked=True)
    assert (result.optimized_layout.components["R2"].x, result.optimized_layout.components["R2"].y) != (100, 100)
    assert result.comparison.optimized_score.hard_violation_count < result.comparison.initial_score.hard_violation_count
    assert result.comparison.optimized_score.locked_obstacle_penalty == 0


def test_multiple_overlapping_controllers_are_separated_deterministically() -> None:
    lib = library()
    circuit = Circuit(
        name="Controllers",
        components=[
            ComponentInstance(ref="U1", component_id="MCU_ATtiny402_SOIC8"),
            ComponentInstance(ref="U2", component_id="MCU_ATtiny402_SOIC8"),
        ],
        nets=[],
    )
    initial = layout_at({"U1": (400, 400), "U2": (400, 400)})

    first = ConstraintPlacementOptimizer().optimize(circuit, lib, initial)
    second = ConstraintPlacementOptimizer().optimize(circuit, lib, initial)

    assert first.optimized_layout.components["U1"] != first.optimized_layout.components["U2"]
    assert first.comparison.optimized_score.hard_violation_count == 0
    assert first.optimized_layout.components == second.optimized_layout.components
    assert first.comparison.moves == second.comparison.moves


def test_off_and_score_only_modes_do_not_move_components() -> None:
    lib = library()
    circuit = two_resistors()
    initial = layout_at({"R1": (100, 100), "R2": (100, 100)})

    off = ConstraintPlacementOptimizer(PlacementOptimizationConfig(mode="off")).optimize(circuit, lib, initial)
    score_only = ConstraintPlacementOptimizer(PlacementOptimizationConfig(mode="score_only")).optimize(circuit, lib, initial)

    assert off.optimized_layout.components == initial.components
    assert score_only.optimized_layout.components == initial.components
    assert score_only.comparison.initial_score.hard_violation_count > 0


def test_budget_limit_is_reported() -> None:
    lib = library()
    circuit = two_resistors()
    initial = layout_at({"R1": (100, 100), "R2": (100, 100)})
    config = PlacementOptimizationConfig(max_total_evaluations=1, max_candidates_per_unit=8)
    result = ConstraintPlacementOptimizer(config).optimize(circuit, lib, initial)

    assert result.comparison.candidate_evaluations <= 1
    assert result.comparison.budget_reached


def test_engine_score_only_mode_preserves_heuristic_coordinates() -> None:
    lib = library()
    circuit = parse_text((ROOT / "examples" / "solar_led.cnet").read_text(encoding="utf-8"))
    off_layout = DeterministicPlacementEngine(optimization_config=PlacementOptimizationConfig(mode="off")).place(circuit, lib)
    score_layout = DeterministicPlacementEngine(optimization_config=PlacementOptimizationConfig(mode="score_only")).place(circuit, lib)

    assert {ref: (p.x, p.y, p.rotation) for ref, p in off_layout.components.items()} == {
        ref: (p.x, p.y, p.rotation) for ref, p in score_layout.components.items()
    }
    assert score_layout.canvas["placement_optimizer"]["mode"] == "score_only"


def test_strong_topology_groups_remain_intact_when_only_soft_constraints_exist() -> None:
    lib = library()
    circuit, diagnostics = NetlistParser().parse_file(ROOT / "test_circuits" / "good" / "04_repeated_groups" / "multi_mosfet_2_channel.cnet")
    assert circuit is not None, diagnostics
    analysis = TopologyAnalyzer().analyze(circuit, lib)
    initial = DeterministicPlacementEngine(optimization_config=PlacementOptimizationConfig(mode="off")).place(circuit, lib)
    result = ConstraintPlacementOptimizer().optimize(circuit, lib, initial, analysis)

    assert result.comparison.initial_score.hard_violation_count == 0
    assert result.optimized_layout.components == initial.components
    for pattern in analysis.patterns_by_type(PatternType.LOW_SIDE_MOSFET_SWITCH):
        refs = sorted(pattern.component_refs)
        anchor = refs[0]
        initial_offsets = {
            ref: (
                initial.components[ref].x - initial.components[anchor].x,
                initial.components[ref].y - initial.components[anchor].y,
            )
            for ref in refs
            if ref in initial.components
        }
        final_offsets = {
            ref: (
                result.optimized_layout.components[ref].x - result.optimized_layout.components[anchor].x,
                result.optimized_layout.components[ref].y - result.optimized_layout.components[anchor].y,
            )
            for ref in refs
            if ref in result.optimized_layout.components
        }
        assert initial_offsets == final_offsets


def test_post_routing_visual_drc_is_clean_for_normal_example() -> None:
    lib = library()
    circuit = parse_text((ROOT / "examples" / "555_timer_50_duty_astable.cnet").read_text(encoding="utf-8"))
    layout = DeterministicPlacementEngine().place(circuit, lib)
    routes = ManhattanRouter().route(circuit, lib, layout)
    diagnostics, metrics = visual_drc(circuit, lib, layout, routes)

    assert [diag.code for diag in diagnostics] == []
    assert metrics["component_body_overlaps"] == 0
    assert metrics["wire_symbol_overlaps"] == 0


def test_renamed_equivalent_circuit_keeps_relative_placement_shape() -> None:
    lib = library()
    original = parse_text("""CIRCUIT RenameA
COMPONENT R1 BASIC_RESISTOR value=1kohm
COMPONENT R2 BASIC_RESISTOR value=2kohm
NET A:
    R1.2
    R2.1
""")
    renamed = parse_text("""CIRCUIT RenameB
COMPONENT RA BASIC_RESISTOR value=1kohm
COMPONENT RB BASIC_RESISTOR value=2kohm
NET A:
    RA.2
    RB.1
""")

    original_layout = DeterministicPlacementEngine().place(original, lib)
    renamed_layout = DeterministicPlacementEngine().place(renamed, lib)

    original_delta = original_layout.components["R2"].x - original_layout.components["R1"].x, original_layout.components["R2"].y - original_layout.components["R1"].y
    renamed_delta = renamed_layout.components["RB"].x - renamed_layout.components["RA"].x, renamed_layout.components["RB"].y - renamed_layout.components["RA"].y
    assert original_delta == renamed_delta
