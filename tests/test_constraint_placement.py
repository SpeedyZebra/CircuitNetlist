from __future__ import annotations

from pathlib import Path

from circuit_netlist.component_library import load_component_library
from circuit_netlist.constraint_placement import ConstraintPlacementOptimizer, PlacementOptimizationConfig, PlacementScorer, _diagnostic_counts
from circuit_netlist.erc import ElectricalRuleChecker
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
    assert result.comparison.optimized_evaluation is not None
    assert result.comparison.optimized_evaluation.valid
    assert result.comparison.optimized_evaluation.final_score <= result.comparison.initial_evaluation.final_score
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


def test_route_validated_candidate_introducing_wire_symbol_overlap_is_rejected() -> None:
    lib = library()
    circuit = parse_text((ROOT / "examples" / "solar_led.cnet").read_text(encoding="utf-8"))
    analysis = TopologyAnalyzer().analyze(circuit, lib)
    initial = DeterministicPlacementEngine(optimization_config=PlacementOptimizationConfig(mode="off")).place(circuit, lib)
    optimizer = ConstraintPlacementOptimizer(PlacementOptimizationConfig(mode="score_only"))
    baseline = optimizer.evaluate_layout(circuit, lib, initial, analysis, initial_layout=initial)
    assert [diag.code for diag in baseline.visual_diagnostics] == []

    candidate = initial.model_copy(deep=True)
    candidate.components["U1"].x = 480
    candidate_score = optimizer.scorer.score(circuit, lib, candidate, analysis, fixed_layout=initial)
    evaluation = optimizer.evaluate_layout(circuit, lib, candidate, analysis, candidate_score, baseline_counts=_diagnostic_counts(baseline.visual_diagnostics), initial_layout=initial)

    assert evaluation.valid is False
    assert "NEW_VISUAL_DRC" in evaluation.rejection_reasons
    assert "DRC_WIRE_SYMBOL_OVERLAP" in [diag.code for diag in evaluation.unexpected_visual_diagnostics]


def test_route_validated_candidate_with_component_overlap_is_rejected() -> None:
    lib = library()
    circuit = two_resistors()
    initial = layout_at({"R1": (100, 100), "R2": (360, 100)})
    optimizer = ConstraintPlacementOptimizer(PlacementOptimizationConfig(mode="score_only"))
    analysis = TopologyAnalyzer().analyze(circuit, lib)
    baseline = optimizer.evaluate_layout(circuit, lib, initial, analysis, initial_layout=initial)

    candidate = layout_at({"R1": (100, 100), "R2": (100, 100)})
    candidate_score = optimizer.scorer.score(circuit, lib, candidate, analysis, fixed_layout=initial)
    evaluation = optimizer.evaluate_layout(circuit, lib, candidate, analysis, candidate_score, baseline_counts=_diagnostic_counts(baseline.visual_diagnostics), initial_layout=initial)

    assert evaluation.valid is False
    assert "HARD_CONSTRAINT_VIOLATION" in evaluation.rejection_reasons


def test_route_validated_candidate_with_routing_failure_is_rejected() -> None:
    lib = library()
    circuit = two_resistors()
    initial = layout_at({"R1": (100, 100), "R2": (360, 100)})
    missing = Layout(components={"R1": Placement(x=100, y=100)}, canvas={"grid": 40, "width": 800, "height": 600})
    optimizer = ConstraintPlacementOptimizer(PlacementOptimizationConfig(mode="score_only"))
    analysis = TopologyAnalyzer().analyze(circuit, lib)
    baseline = optimizer.evaluate_layout(circuit, lib, initial, analysis, initial_layout=initial)
    evaluation = optimizer.evaluate_layout(circuit, lib, missing, analysis, baseline_counts=_diagnostic_counts(baseline.visual_diagnostics), initial_layout=initial)

    assert evaluation.valid is False
    assert evaluation.routing_succeeded is False
    assert "ROUTING_FAILED" in evaluation.rejection_reasons


def test_soft_optimization_uses_actual_routed_metrics_and_preserves_visual_drc() -> None:
    lib = library()
    circuit = parse_text((ROOT / "examples" / "555_timer_50_duty_astable.cnet").read_text(encoding="utf-8"))
    analysis = TopologyAnalyzer().analyze(circuit, lib)
    initial = DeterministicPlacementEngine(optimization_config=PlacementOptimizationConfig(mode="off")).place(circuit, lib)
    result = ConstraintPlacementOptimizer().optimize(circuit, lib, initial, analysis)

    assert result.comparison.route_validations > 0
    assert result.comparison.optimized_evaluation.valid
    assert [diag.code for diag in result.comparison.optimized_evaluation.visual_diagnostics] == []
    assert result.comparison.optimized_evaluation.final_score <= result.comparison.initial_evaluation.final_score
    assert result.comparison.optimized_evaluation.total_routed_length <= result.comparison.initial_evaluation.total_routed_length


def test_soft_optimization_no_worse_for_known_risky_circuits() -> None:
    for relative in [
        "test_circuits/good/04_repeated_groups/multi_mosfet_4_channel.cnet",
        "test_circuits/good/04_repeated_groups/multi_decoupling_with_rc_filters.cnet",
        "examples/solar_led.cnet",
    ]:
        lib = library()
        circuit, diagnostics = NetlistParser().parse_file(ROOT / relative)
        assert circuit is not None, diagnostics
        analysis = TopologyAnalyzer().analyze(circuit, lib)
        initial = DeterministicPlacementEngine(optimization_config=PlacementOptimizationConfig(mode="off")).place(circuit, lib)
        result = ConstraintPlacementOptimizer().optimize(circuit, lib, initial, analysis)
        baseline_codes = sorted(diag.code for diag in result.comparison.initial_evaluation.visual_diagnostics if diag.code)
        optimized_codes = sorted(diag.code for diag in result.comparison.optimized_evaluation.visual_diagnostics if diag.code)
        assert optimized_codes == baseline_codes
        assert result.comparison.optimized_evaluation.final_score <= result.comparison.initial_evaluation.final_score


def test_hard_only_clean_layout_uses_fast_path() -> None:
    lib = library()
    circuit = two_resistors()
    initial = layout_at({"R1": (100, 100), "R2": (360, 100)})
    result = ConstraintPlacementOptimizer(PlacementOptimizationConfig(optimize_soft_constraints=False)).optimize(circuit, lib, initial)

    assert result.optimized_layout.components == initial.components
    assert result.comparison.fast_path == "hard_only_clean_layout"
    assert result.comparison.candidate_evaluations == 0
    assert result.comparison.route_validations == 0


def test_all_locked_layout_uses_no_movable_fast_path() -> None:
    lib = library()
    circuit = two_resistors()
    initial = layout_at({"R1": (100, 100), "R2": (360, 100)}, locked={"R1", "R2"})
    result = ConstraintPlacementOptimizer().optimize(circuit, lib, initial)

    assert result.comparison.fast_path == "no_movable_components"
    assert result.optimized_layout.components == initial.components


def test_route_validation_budget_exhaustion_returns_best_valid_layout() -> None:
    lib = library()
    circuit = parse_text((ROOT / "examples" / "555_timer_50_duty_astable.cnet").read_text(encoding="utf-8"))
    analysis = TopologyAnalyzer().analyze(circuit, lib)
    initial = DeterministicPlacementEngine(optimization_config=PlacementOptimizationConfig(mode="off")).place(circuit, lib)
    result = ConstraintPlacementOptimizer(PlacementOptimizationConfig(max_total_route_validations=1, max_route_validations_per_pass=1)).optimize(circuit, lib, initial, analysis)

    assert result.comparison.budget_reached
    assert result.comparison.budget_reason in {"MAX_ROUTE_VALIDATIONS", "MAX_ROUTE_VALIDATIONS_PER_PASS"}
    assert result.comparison.optimized_evaluation.valid


def test_conservative_orientation_optimization_for_loose_two_pin_passives() -> None:
    lib = library()
    circuit = two_resistors()
    analysis = TopologyAnalyzer().analyze(circuit, lib)
    initial = layout_at({"R1": (100, 100), "R2": (100, 360)})
    result = ConstraintPlacementOptimizer(PlacementOptimizationConfig(candidate_radii=(), max_total_route_validations=10)).optimize(circuit, lib, initial, analysis)

    assert result.optimized_layout.components["R1"].rotation == 90
    assert result.optimized_layout.components["R2"].rotation == 90
    assert result.comparison.optimized_evaluation.total_routed_length < result.comparison.initial_evaluation.total_routed_length


def test_strong_topology_orientation_is_preserved() -> None:
    lib = library()
    circuit = parse_text((ROOT / "examples" / "solar_led.cnet").read_text(encoding="utf-8"))
    layout = DeterministicPlacementEngine().place(circuit, lib)

    assert layout.components["R_BAT_TOP"].rotation == 90
    assert layout.components["R_BAT_BOT"].rotation == 90
    assert layout.components["R_GATE"].rotation == 0
    assert layout.components["C1"].rotation == 90


def test_optimizer_preserves_expected_electrical_diagnostics_and_connectivity() -> None:
    lib = library()
    circuit = parse_text("""CIRCUIT ReversedLed
COMPONENT V1 POWER_DC_SOURCE voltage=5V
COMPONENT R1 BASIC_RESISTOR value=330ohm role=current_limit
COMPONENT LED1 LIGHT_LED_RED

NET VCC:
    V1.POS
    R1.1

NET LED_NODE:
    R1.2
    LED1.K

NET GND:
    V1.NEG
    LED1.A
""")
    initial_erc = [diag.code for diag in ElectricalRuleChecker(lib).check(circuit)]
    before_signature = [(net.name, sorted((pin.component_ref, pin.pin_name) for pin in net.pins)) for net in circuit.nets]
    layout = DeterministicPlacementEngine().place(circuit, lib)
    after_signature = [(net.name, sorted((pin.component_ref, pin.pin_name) for pin in net.pins)) for net in circuit.nets]
    final_erc = [diag.code for diag in ElectricalRuleChecker(lib).check(circuit)]

    assert "ERC_LED_POLARITY_REVERSED" in initial_erc
    assert final_erc == initial_erc
    assert after_signature == before_signature
    assert layout.components


def two_controller_text() -> str:
    return """CIRCUIT TwoControllers
COMPONENT V1 POWER_DC_SOURCE voltage=5V role=source
COMPONENT U1 MCU_ATtiny402_SOIC8 role=controller
COMPONENT U2 BASIC_555_TIMER role=controller
COMPONENT R_LED1 BASIC_RESISTOR value=220ohm role=current_limit
COMPONENT D1 LIGHT_LED_RED
COMPONENT R_GATE1 BASIC_RESISTOR value=100ohm role=gate_resistor
COMPONENT R_PULL1 BASIC_RESISTOR value=100kohm role=pulldown
COMPONENT Q1 BASIC_NMOS role=low_side_switch
COMPONENT R_LED2 BASIC_RESISTOR value=220ohm role=current_limit
COMPONENT D2 LIGHT_LED_GREEN
COMPONENT R_GATE2 BASIC_RESISTOR value=100ohm role=gate_resistor
COMPONENT R_PULL2 BASIC_RESISTOR value=100kohm role=pulldown
COMPONENT Q2 BASIC_NMOS role=low_side_switch

NET VCC:
    V1.POS
    U1.VDD
    U2.VCC
    U2.RESET
    R_LED1.1
    R_LED2.1

NET A_NODE:
    R_LED1.2
    D1.A

NET A_SWITCH:
    D1.K
    Q1.D

NET A_CTRL:
    U1.PA7
    R_GATE1.1

NET A_GATE:
    R_GATE1.2
    R_PULL1.1
    Q1.G

NET B_NODE:
    R_LED2.2
    D2.A

NET B_SWITCH:
    D2.K
    Q2.D

NET B_CTRL:
    U2.OUT
    R_GATE2.1

NET B_GATE:
    R_GATE2.2
    R_PULL2.1
    Q2.G

NET TIMER_TIMING:
    U2.TRIG
    U2.THRESH

NET TIMER_CTRL [allow_single]:
    U2.CTRL

NET GND:
    V1.NEG
    U1.GND
    U2.GND
    R_PULL1.2
    Q1.S
    R_PULL2.2
    Q2.S
"""


def test_two_real_controllers_driving_separate_channels_are_route_validated() -> None:
    lib = library()
    circuit = parse_text(two_controller_text())
    analysis = TopologyAnalyzer().analyze(circuit, lib)
    layout = DeterministicPlacementEngine().place(circuit, lib)
    layout_again = DeterministicPlacementEngine().place(circuit, lib)
    routes = ManhattanRouter().route(circuit, lib, layout)
    diagnostics, metrics = visual_drc(circuit, lib, layout, routes)

    assert layout.model_dump(mode="json") == layout_again.model_dump(mode="json")
    assert layout.components["U1"] != layout.components["U2"]
    assert metrics["component_body_overlaps"] == 0
    assert not [diag for diag in diagnostics if diag.severity in {"ERROR", "FATAL"}]
    assert analysis.component_roles["U1"].value == "controller"
    assert analysis.component_roles["U2"].value == "controller"


def test_three_controllers_sharing_supply_rails_do_not_collapse() -> None:
    text = """CIRCUIT ThreeControllers
COMPONENT V1 POWER_DC_SOURCE voltage=5V role=source
COMPONENT U1 MCU_ATtiny402_SOIC8 role=controller
COMPONENT U2 MCU_ATtiny402_SOIC8 role=controller
COMPONENT U3 MCU_ATtiny402_SOIC8 role=controller
COMPONENT R_LED1 BASIC_RESISTOR value=220ohm role=current_limit
COMPONENT D1 LIGHT_LED_RED
COMPONENT R_GATE1 BASIC_RESISTOR value=100ohm role=gate_resistor
COMPONENT R_PULL1 BASIC_RESISTOR value=100kohm role=pulldown
COMPONENT Q1 BASIC_NMOS role=low_side_switch
COMPONENT R_LED2 BASIC_RESISTOR value=220ohm role=current_limit
COMPONENT D2 LIGHT_LED_GREEN
COMPONENT R_GATE2 BASIC_RESISTOR value=100ohm role=gate_resistor
COMPONENT R_PULL2 BASIC_RESISTOR value=100kohm role=pulldown
COMPONENT Q2 BASIC_NMOS role=low_side_switch
COMPONENT R_LED3 BASIC_RESISTOR value=220ohm role=current_limit
COMPONENT D3 LIGHT_LED_BLUE
COMPONENT R_GATE3 BASIC_RESISTOR value=100ohm role=gate_resistor
COMPONENT R_PULL3 BASIC_RESISTOR value=100kohm role=pulldown
COMPONENT Q3 BASIC_NMOS role=low_side_switch

NET VCC:
    V1.POS
    U1.VDD
    U2.VDD
    U3.VDD
    R_LED1.1
    R_LED2.1
    R_LED3.1

NET A_NODE:
    R_LED1.2
    D1.A

NET A_SWITCH:
    D1.K
    Q1.D

NET A_CTRL:
    U1.PA7
    R_GATE1.1

NET A_GATE:
    R_GATE1.2
    R_PULL1.1
    Q1.G

NET B_NODE:
    R_LED2.2
    D2.A

NET B_SWITCH:
    D2.K
    Q2.D

NET B_CTRL:
    U2.PA7
    R_GATE2.1

NET B_GATE:
    R_GATE2.2
    R_PULL2.1
    Q2.G

NET C_NODE:
    R_LED3.2
    D3.A

NET C_SWITCH:
    D3.K
    Q3.D

NET C_CTRL:
    U3.PA7
    R_GATE3.1

NET C_GATE:
    R_GATE3.2
    R_PULL3.1
    Q3.G

NET GND:
    V1.NEG
    U1.GND
    U2.GND
    U3.GND
    R_PULL1.2
    Q1.S
    R_PULL2.2
    Q2.S
    R_PULL3.2
    Q3.S
"""
    lib = library()
    circuit = parse_text(text)
    layout = DeterministicPlacementEngine().place(circuit, lib)
    controller_positions = {(layout.components[ref].x, layout.components[ref].y) for ref in ["U1", "U2", "U3"]}
    routes = ManhattanRouter().route(circuit, lib, layout)
    diagnostics, metrics = visual_drc(circuit, lib, layout, routes)

    assert len(controller_positions) == 3
    assert metrics["component_body_overlaps"] == 0
    assert not [diag for diag in diagnostics if diag.severity in {"ERROR", "FATAL"}]
