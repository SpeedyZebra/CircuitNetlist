from __future__ import annotations

from pathlib import Path

import pytest

from circuit_netlist.component_library import ComponentLibrary, load_component_library
from circuit_netlist.geometry import component_size
from circuit_netlist.models import Circuit, ComponentInstance, Layout, Net, PinRef, Placement
from circuit_netlist.parser import NetlistParser
from circuit_netlist.placement import DeterministicPlacementEngine, get_oriented_component_bounds
from circuit_netlist.regression import visual_drc
from circuit_netlist.renderer import render_circuit
from circuit_netlist.router import ManhattanRouter
from circuit_netlist.topology import PatternType, TopologyAnalyzer
from circuit_netlist.validator import CircuitValidator, has_blocking_diagnostics


ROOT = Path(__file__).resolve().parents[1]
pytestmark = [pytest.mark.integration, pytest.mark.slow]
STRESS_ROOT = ROOT / "test_circuits" / "good" / "04_repeated_groups"


def variant_library(
    *,
    resistor: tuple[int, int] | None = None,
    led: tuple[int, int] | None = None,
    capacitor: tuple[int, int] | None = None,
    mosfet: tuple[int, int] | None = None,
    controller: tuple[int, int] | None = None,
) -> ComponentLibrary:
    library = load_component_library(ROOT / "components")
    replacements = {
        "BASIC_RESISTOR": resistor,
        "LIGHT_LED_RED": led,
        "LIGHT_LED_GREEN": led,
        "LIGHT_LED_BLUE": led,
        "LIGHT_LED_WHITE": led,
        "BASIC_CAPACITOR_CERAMIC": capacitor,
        "BASIC_CAPACITOR_POLARIZED": capacitor,
        "BASIC_NMOS": mosfet,
        "MCU_ATtiny402_SOIC8": controller,
    }
    for component_id, size in replacements.items():
        if size and component_id in library.components:
            definition = library.components[component_id]
            library.components[component_id] = definition.model_copy(update={"body": definition.body.model_copy(update={"width": size[0], "height": size[1]})})
    return library


def parse_case(filename: str):
    circuit, diagnostics = NetlistParser().parse_file(STRESS_ROOT / filename)
    assert circuit is not None, diagnostics
    return circuit


def stable_layout_dump(layout: Layout) -> dict[str, object]:
    canvas = dict(layout.canvas)
    optimizer = canvas.get("placement_optimizer")
    if isinstance(optimizer, dict):
        canvas["placement_optimizer"] = {
            "mode": optimizer.get("mode"),
            "optimize_soft_constraints": optimizer.get("optimize_soft_constraints"),
            "optimized": optimizer.get("optimized"),
            "fast_path": optimizer.get("fast_path"),
        }
    return {
        "components": layout.model_dump(mode="json")["components"],
        "nets": layout.model_dump(mode="json")["nets"],
        "wire_waypoints": layout.model_dump(mode="json")["wire_waypoints"],
        "canvas": canvas,
    }


def run_pipeline(circuit, library: ComponentLibrary, existing: Layout | None = None):
    validation = CircuitValidator(library).validate(circuit)
    assert not has_blocking_diagnostics(validation), validation
    analysis = TopologyAnalyzer().analyze(circuit, library)
    engine = DeterministicPlacementEngine()
    layout = engine.place(circuit, library, existing)
    assert stable_layout_dump(layout) == stable_layout_dump(DeterministicPlacementEngine().place(circuit, library, existing))
    routes = ManhattanRouter(validate_auto_route_styles=False).route(circuit, library, layout)
    drc, metrics = visual_drc(circuit, library, layout, routes)
    render_circuit(circuit, library, layout, routes, [*validation, *drc])
    return analysis, layout, drc, metrics, engine.last_group_envelopes


def assert_clean(circuit, library: ComponentLibrary, existing: Layout | None = None):
    analysis, layout, drc, metrics, envelopes = run_pipeline(circuit, library, existing)
    assert [diag.code for diag in drc] == []
    assert metrics["component_body_overlaps"] == 0
    assert metrics["wire_symbol_overlaps"] == 0
    assert metrics["unrelated_wire_overlaps"] == 0
    assert metrics["physical_wire_text_overlaps"] == 0
    return analysis, layout, envelopes


@pytest.mark.parametrize("size", [(180, 120), (220, 140), (280, 180)])
def test_two_mosfet_channels_expand_for_oversized_resistors_and_leds(size: tuple[int, int]) -> None:
    circuit = parse_case("multi_mosfet_2_channel.cnet")
    library = variant_library(resistor=size, led=size)
    analysis, _, envelopes = assert_clean(circuit, library)
    assert len(analysis.patterns_by_type(PatternType.LOW_SIDE_MOSFET_SWITCH)) == 2
    heights = [group["expanded_envelope"]["height"] for group in envelopes if group["pattern_type"] == "low_side_mosfet_switch"]
    assert min(heights) > 700


@pytest.mark.parametrize("size", [(180, 120), (220, 140), (280, 180)])
def test_four_mosfet_channels_expand_for_oversized_resistors_and_leds(size: tuple[int, int]) -> None:
    circuit = parse_case("multi_mosfet_4_channel.cnet")
    library = variant_library(resistor=size, led=size)
    analysis, layout, envelopes = assert_clean(circuit, library)
    assert len(analysis.patterns_by_type(PatternType.LOW_SIDE_MOSFET_SWITCH)) == 4
    assert len({layout.components[ref].y for ref in ["Q12", "Q47", "Q5", "Q88"]}) == 4
    heights = [group["expanded_envelope"]["height"] for group in envelopes if group["pattern_type"] == "low_side_mosfet_switch"]
    assert len(heights) == 4


def test_mosfet_channels_with_different_component_sizes_get_different_envelopes() -> None:
    circuit = parse_case("multi_mosfet_2_channel.cnet")
    library = variant_library(resistor=(220, 140), led=(280, 180), mosfet=(180, 180))
    _, _, envelopes = assert_clean(circuit, library)
    low_side = [group for group in envelopes if group["pattern_type"] == "low_side_mosfet_switch"]
    assert low_side[0]["expanded_envelope"]["height"] >= 900


def test_variable_size_voltage_dividers_and_rc_filters_render_cleanly() -> None:
    divider = parse_case("multi_voltage_divider.cnet")
    rc_filter = parse_case("multi_rc_filter.cnet")
    library = variant_library(resistor=(280, 180), capacitor=(220, 180))
    divider_analysis, divider_layout, divider_envelopes = assert_clean(divider, library)
    rc_analysis, rc_layout, rc_envelopes = assert_clean(rc_filter, library)
    assert len(divider_analysis.patterns_by_type(PatternType.VOLTAGE_DIVIDER)) == 4
    assert len(rc_analysis.patterns_by_type(PatternType.RC_LOWPASS)) == 2
    assert len({divider_layout.components[pattern.metadata["top_resistor"]].x for pattern in divider_analysis.patterns_by_type(PatternType.VOLTAGE_DIVIDER)}) == 4
    assert all(group["expanded_envelope"]["width"] > 300 for group in divider_envelopes if group["pattern_type"] == "voltage_divider")
    assert all(group["expanded_envelope"]["height"] > 600 for group in rc_envelopes if group["pattern_type"] == "rc_lowpass")


def test_variable_size_decoupling_bank_and_large_controller_render_cleanly() -> None:
    circuit = parse_case("multi_decoupling_with_rc_filters.cnet")
    library = variant_library(capacitor=(260, 200), controller=(300, 260))
    analysis, layout, _ = assert_clean(circuit, library)
    assert len(analysis.patterns_by_type(PatternType.DECOUPLING_CAPACITOR)) == 2
    assert layout.components["C7"].x < layout.components["C54"].x


def test_rotated_component_effective_bounds_match_orientation() -> None:
    library = variant_library(resistor=(280, 180))
    component = ComponentInstance(ref="R1", component_id="BASIC_RESISTOR")
    zero = get_oriented_component_bounds(component, 0, 0, 0, library)
    ninety = get_oriented_component_bounds(component, 0, 0, 90, library)
    one_eighty = get_oriented_component_bounds(component, 0, 0, 180, library)
    two_seventy = get_oriented_component_bounds(component, 0, 0, 270, library)
    assert (zero.width, zero.height) == (280, 180)
    assert (ninety.width, ninety.height) == (180, 280)
    assert (one_eighty.width, one_eighty.height) == (280, 180)
    assert (two_seventy.width, two_seventy.height) == (180, 280)


def test_locked_rotated_oversized_component_remains_fixed() -> None:
    circuit = parse_case("multi_mosfet_2_channel.cnet")
    library = variant_library(resistor=(220, 140), led=(220, 140), mosfet=(220, 180))
    existing = Layout(components={"Q47": Placement(x=2500, y=2100, rotation=90, locked=True)})
    _, layout, _ = assert_clean(circuit, library, existing)
    assert layout.components["Q47"] == Placement(x=2500, y=2100, rotation=90, locked=True)


def test_missing_geometry_uses_fallback_bounds_in_full_pipeline() -> None:
    circuit = parse_case("multi_mosfet_2_channel.cnet")
    library = variant_library()
    resistor_definition = library.components["BASIC_RESISTOR"]
    library.components["BASIC_RESISTOR"] = resistor_definition.model_copy(update={"body": resistor_definition.body.model_copy(update={"width": 0, "height": 0})})
    _, _, envelopes = assert_clean(circuit, library)
    low_side = [group for group in envelopes if group["pattern_type"] == "low_side_mosfet_switch"]
    assert any(group["fallback_geometry_refs"] for group in low_side)


def test_wide_and_tall_controllers_preserve_clean_channel_layout() -> None:
    circuit = parse_case("multi_mosfet_4_channel.cnet")
    assert_clean(circuit, variant_library(controller=(340, 220), resistor=(180, 120), led=(180, 120)))
    assert_clean(circuit, variant_library(controller=(220, 420), resistor=(180, 120), led=(180, 120)))


def test_renamed_oversized_equivalents_keep_relative_group_layout() -> None:
    original = parse_case("multi_mosfet_2_channel.cnet")
    text = (STRESS_ROOT / "multi_mosfet_2_channel.cnet").read_text(encoding="utf-8")
    renamed_text = (
        text.replace("Q12", "Q91")
        .replace("Q47", "Q13")
        .replace("R81", "R72")
        .replace("R6", "R64")
        .replace("R27", "R55")
        .replace("R19", "R38")
        .replace("R3", "R99")
        .replace("R44", "R21")
        .replace("D14", "D42")
        .replace("D31", "D77")
    )
    renamed, diagnostics = NetlistParser().parse_text(renamed_text)
    assert renamed is not None, diagnostics
    library = variant_library(resistor=(220, 140), led=(220, 140))
    _, original_layout, _ = assert_clean(original, library)
    _, renamed_layout, _ = assert_clean(renamed, library)
    original_rows = sorted({placement.y for ref, placement in original_layout.components.items() if ref.startswith("Q")})
    renamed_rows = sorted({placement.y for ref, placement in renamed_layout.components.items() if ref.startswith("Q")})
    assert original_rows == renamed_rows
