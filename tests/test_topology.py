from __future__ import annotations

from pathlib import Path

from circuit_netlist.component_library import load_component_library
from circuit_netlist.constraint_placement import PlacementOptimizationConfig
from circuit_netlist.models import Layout, Placement
from circuit_netlist.parser import NetlistParser
from circuit_netlist.placement import DeterministicPlacementEngine
from circuit_netlist.topology import CapacitorRole, ComponentRole, PatternType, TopologyAnalyzer
from circuit_netlist.validator import CircuitValidator


ROOT = Path(__file__).resolve().parents[1]


def heuristic_placement_engine() -> DeterministicPlacementEngine:
    return DeterministicPlacementEngine(optimization_config=PlacementOptimizationConfig(mode="off"))


RENAMED_SOLAR = """CIRCUIT Renamed_Solar

COMPONENT PANELX POWER_SOLAR_PANEL_5V_2W
COMPONENT PMIC7 POWER_CHARGER_BQ25185_BLOCK
COMPONENT CELL9 POWER_LIPO_1S capacity=1000mAh
COMPONENT MCU7 MCU_ATtiny402_SOIC8
COMPONENT Q12 BASIC_NMOS
COMPONENT D4 LIGHT_LED_WHITE vf=3.0V current=20mA
COMPONENT R22 BASIC_RESISTOR value=68ohm
COMPONENT R3 BASIC_RESISTOR value=100ohm
COMPONENT R9 BASIC_RESISTOR value=100kohm
COMPONENT R17 BASIC_RESISTOR value=220kohm
COMPONENT R18 BASIC_RESISTOR value=100kohm
COMPONENT R41 BASIC_RESISTOR value=220kohm
COMPONENT R42 BASIC_RESISTOR value=100kohm
COMPONENT CX BASIC_CAPACITOR_CERAMIC value=100nF

NET PANEL_POS:
    PANELX.POS
    PMIC7.VIN
    R17.1

NET VBAT:
    PMIC7.BAT
    CELL9.POS
    MCU7.VDD
    CX.1
    R22.1
    R41.1

NET GND:
    PANELX.NEG
    PMIC7.GND
    CELL9.NEG
    MCU7.GND
    CX.2
    Q12.S
    R9.2
    R18.2
    R42.2

NET SUN_SENSE:
    R17.2
    R18.1
    MCU7.PA6

NET BAT_SENSE:
    R41.2
    R42.1
    MCU7.PA1

NET LED_ANODE:
    R22.2
    D4.A

NET LED_SWITCH:
    D4.K
    Q12.D

NET LED_ENABLE:
    MCU7.PA7
    R3.1
    R9.1

NET MOSFET_GATE:
    R3.2
    Q12.G
"""


RC_LOWPASS = """CIRCUIT Filter

COMPONENT VIN POWER_DC_SOURCE voltage=5V
COMPONENT RA BASIC_RESISTOR value=1kohm
COMPONENT CF BASIC_CAPACITOR_CERAMIC value=100nF
COMPONENT U1 MCU_ATtiny402_SOIC8

NET VIN:
    VIN.POS
    RA.1

NET FILTERED:
    RA.2
    CF.1
    U1.PA6

NET GND:
    VIN.NEG
    CF.2
    U1.GND

NET VDD:
    U1.VDD
    VIN.POS
"""


PULL_UP = """CIRCUIT Pullup

COMPONENT VIN POWER_DC_SOURCE voltage=5V
COMPONENT RP BASIC_RESISTOR value=10kohm
COMPONENT U7 MCU_ATtiny402_SOIC8

NET VDD:
    VIN.POS
    RP.1
    U7.VDD

NET RESET_LINE:
    RP.2
    U7.PA0

NET GND:
    VIN.NEG
    U7.GND
"""


def analyze_text(text: str):
    library = load_component_library(ROOT / "components")
    circuit, diagnostics = NetlistParser().parse_text(text)
    assert circuit is not None, diagnostics
    return circuit, library, TopologyAnalyzer().analyze(circuit, library)


def patterns(analysis, pattern_type: PatternType):
    return [pattern for pattern in analysis.patterns if pattern.pattern_type == pattern_type]


def test_component_role_inference_does_not_depend_on_reference_names() -> None:
    circuit, library, analysis = analyze_text(RENAMED_SOLAR)
    assert analysis.component_roles["PANELX"] == ComponentRole.SOURCE
    assert analysis.component_roles["PMIC7"] == ComponentRole.CHARGER
    assert analysis.component_roles["CELL9"] == ComponentRole.STORAGE
    assert analysis.component_roles["MCU7"] == ComponentRole.CONTROLLER
    assert analysis.component_roles["Q12"] == ComponentRole.SWITCH
    assert analysis.component_roles["D4"] == ComponentRole.LOAD


def test_every_explicit_component_role_is_supported_and_exposed() -> None:
    for role in ComponentRole:
        text = f"""CIRCUIT Role_{role.value}
COMPONENT X BASIC_TEST_POINT role={role.value}
NET N [allow_single]:
    X.TP
"""
        _, _, analysis = analyze_text(text)
        assert analysis.component_roles["X"] == role
        assert analysis.component_role_sources["X"] == "explicit"
        assert analysis.component_role_reasons["X"] == f"role={role.value}"


def test_explicit_role_overrides_library_inference() -> None:
    _, _, analysis = analyze_text("""CIRCUIT Explicit
COMPONENT U7 MCU_ATtiny402_SOIC8 role=sensor
NET VDD [allow_single]:
    U7.VDD
NET GND [allow_single]:
    U7.GND
""")
    assert analysis.component_roles["U7"] == ComponentRole.SENSOR
    assert analysis.component_role_sources["U7"] == "explicit"


def test_invalid_explicit_role_produces_validation_warning() -> None:
    library = load_component_library(ROOT / "components")
    circuit, diagnostics = NetlistParser().parse_text("""CIRCUIT BadRole
COMPONENT TP BASIC_TEST_POINT role=banana_mode
NET N [allow_single]:
    TP.TP
""")
    assert circuit is not None, diagnostics
    validation = CircuitValidator(library).validate(circuit)
    assert any(diag.code == "VALIDATION_UNKNOWN_ROLE" and diag.severity == "WARNING" for diag in validation)


def test_net_name_classification_is_ground_first_and_conservative() -> None:
    net_lines = "\n".join(
        f"NET {name} [allow_single]:\n    TP.TP"
        for name in ["0V", "GND", "DGND", "AGND", "PGND", "SGND", "CHASSIS_GND", "VSS", "3V3", "5V", "12V", "-12V", "VBAT", "VCC", "VDD", "ADC_5V_SENSE", "MOTOR_12V_SENSE"]
    )
    circuit, diagnostics = NetlistParser().parse_text(f"CIRCUIT Nets\nCOMPONENT TP BASIC_TEST_POINT\n{net_lines}\n")
    assert circuit is not None, diagnostics
    analysis = TopologyAnalyzer().analyze(circuit, load_component_library(ROOT / "components"))
    for name in ["0V", "GND", "DGND", "AGND", "PGND", "SGND", "CHASSIS_GND", "VSS"]:
        assert name in analysis.ground_nets
        assert name not in analysis.power_nets
    for name in ["3V3", "5V", "12V", "-12V", "VBAT", "VCC", "VDD"]:
        assert name in analysis.power_nets
        assert name not in analysis.ground_nets
    assert "ADC_5V_SENSE" not in analysis.power_nets
    assert "MOTOR_12V_SENSE" not in analysis.power_nets


def test_topology_patterns_work_with_arbitrary_component_names() -> None:
    _, _, analysis = analyze_text(RENAMED_SOLAR)
    assert len(patterns(analysis, PatternType.VOLTAGE_DIVIDER)) == 2
    assert {pattern.metadata["midpoint_net"] for pattern in patterns(analysis, PatternType.VOLTAGE_DIVIDER)} == {"SUN_SENSE", "BAT_SENSE"}
    assert patterns(analysis, PatternType.PULL_DOWN)[0].metadata["resistor"] == "R9"
    assert patterns(analysis, PatternType.SERIES_GATE_RESISTOR)[0].metadata["gate_resistor"] == "R3"
    assert patterns(analysis, PatternType.LED_CURRENT_LIMIT)[0].metadata["resistor"] == "R22"
    assert patterns(analysis, PatternType.LOW_SIDE_MOSFET_SWITCH)[0].metadata["mosfet"] == "Q12"
    assert patterns(analysis, PatternType.DECOUPLING_CAPACITOR)[0].metadata["capacitor"] == "CX"


def test_pull_up_detection_works_with_arbitrary_names() -> None:
    _, _, analysis = analyze_text(PULL_UP)
    pullups = patterns(analysis, PatternType.PULL_UP)
    assert len(pullups) == 1
    assert pullups[0].metadata["resistor"] == "RP"
    assert pullups[0].metadata["signal_net"] == "RESET_LINE"


def test_rc_lowpass_detection_works() -> None:
    _, _, analysis = analyze_text(RC_LOWPASS)
    rc = patterns(analysis, PatternType.RC_LOWPASS)
    assert len(rc) == 1
    assert rc[0].metadata["resistor"] == "RA"
    assert rc[0].metadata["capacitor"] == "CF"


def test_flyback_detection_works_with_basic_diode() -> None:
    _, _, analysis = analyze_text("""CIRCUIT Flyback
COMPONENT L1 BASIC_INDUCTOR value=10mH
COMPONENT D1 BASIC_DIODE
NET VCC:
    L1.1
    D1.K
NET SWITCH:
    L1.2
    D1.A
""")
    flybacks = patterns(analysis, PatternType.FLYBACK_DIODE)
    assert len(flybacks) == 1
    assert flybacks[0].metadata["diode"] == "D1"
    assert flybacks[0].metadata["load"] == "L1"


def test_renamed_solar_layout_preserves_relative_topology() -> None:
    circuit, library, analysis = analyze_text(RENAMED_SOLAR)
    layout = heuristic_placement_engine().place(circuit, library)
    assert layout.components["PANELX"].x < layout.components["PMIC7"].x < layout.components["MCU7"].x < layout.components["Q12"].x
    assert layout.components["R3"].rotation == 0
    assert layout.components["R9"].rotation == 90
    assert layout.components["R22"].rotation == 0
    assert layout.components["CX"].rotation == 90
    for divider in patterns(analysis, PatternType.VOLTAGE_DIVIDER):
        top = divider.metadata["top_resistor"]
        bottom = divider.metadata["bottom_resistor"]
        assert layout.components[top].rotation == 90
        assert layout.components[bottom].rotation == 90
        assert layout.components[top].x == layout.components[bottom].x
        assert layout.components[top].y < layout.components[bottom].y


def test_placement_is_deterministic_and_locked_positions_remain() -> None:
    circuit, library, _ = analyze_text(RENAMED_SOLAR)
    layout1 = heuristic_placement_engine().place(circuit, library)
    layout2 = heuristic_placement_engine().place(circuit, library)
    assert layout1.model_dump(mode="json") == layout2.model_dump(mode="json")
    existing = Layout(components={"Q12": Placement(x=321, y=654, rotation=90, locked=True)})
    layout3 = heuristic_placement_engine().place(circuit, library, existing)
    assert layout3.components["Q12"] == Placement(x=321, y=654, rotation=90, locked=True)


def test_production_placement_has_no_known_solar_reference_hacks() -> None:
    placement_source = (ROOT / "src" / "circuit_netlist" / "placement.py").read_text(encoding="utf-8")
    forbidden = ["R_SUN_TOP", "R_SUN_BOT", "R_BAT_TOP", "R_BAT_BOT", "R_PULL", "R_GATE", "R_LED"]
    assert not [token for token in forbidden if token in placement_source]


def test_capacitor_role_classification_is_conservative() -> None:
    circuit, library, analysis = analyze_text("""CIRCUIT Caps
COMPONENT VIN POWER_DC_SOURCE voltage=5V
COMPONENT U1 MCU_ATtiny402_SOIC8
COMPONENT C100N BASIC_CAPACITOR_CERAMIC value=100nF
COMPONENT C1U BASIC_CAPACITOR_CERAMIC value=1uF
COMPONENT C10U BASIC_CAPACITOR_CERAMIC value=10uF role=bulk
COMPONENT C100U BASIC_CAPACITOR_CERAMIC value=100uF
COMPONENT C1000U BASIC_CAPACITOR_CERAMIC value=1000uF
COMPONENT CUNK BASIC_CAPACITOR_CERAMIC
COMPONENT CMAL BASIC_CAPACITOR_CERAMIC value=not_a_value

NET VDD:
    VIN.POS
    U1.VDD
    C100N.1
    C1U.1
    C10U.1
    C100U.1
    C1000U.1
    CUNK.1
    CMAL.1

NET GND:
    VIN.NEG
    U1.GND
    C100N.2
    C1U.2
    C10U.2
    C100U.2
    C1000U.2
    CUNK.2
    CMAL.2
""")
    assert circuit is not None
    assert library is not None
    assert analysis.capacitor_roles["C100N"] == CapacitorRole.DECOUPLING
    assert analysis.capacitor_roles["C1U"] == CapacitorRole.BYPASS
    assert analysis.capacitor_roles["C10U"] == CapacitorRole.BULK
    assert analysis.capacitor_role_metadata["C10U"]["confidence"] == 1.0
    assert analysis.capacitor_roles["C100U"] == CapacitorRole.BULK
    assert analysis.capacitor_roles["C1000U"] == CapacitorRole.RESERVOIR
    assert analysis.capacitor_roles["CUNK"] == CapacitorRole.UNKNOWN_POWER_SHUNT
    assert analysis.capacitor_roles["CMAL"] == CapacitorRole.UNKNOWN_POWER_SHUNT
    decoupling_refs = {pattern.metadata["capacitor"] for pattern in patterns(analysis, PatternType.DECOUPLING_CAPACITOR)}
    assert decoupling_refs == {"C100N", "C1U"}


def test_repeated_low_side_switches_get_separate_rows() -> None:
    circuit, library, _ = analyze_text("""CIRCUIT RepeatedSwitches
COMPONENT V1 POWER_DC_SOURCE voltage=5V
COMPONENT U1 MCU_ATtiny402_SOIC8
COMPONENT R_LED1 BASIC_RESISTOR value=220ohm role=current_limit
COMPONENT LED1 LIGHT_LED_RED
COMPONENT R_GATE1 BASIC_RESISTOR value=100ohm role=gate_resistor
COMPONENT R_PULL1 BASIC_RESISTOR value=100kohm role=pulldown
COMPONENT Q1 BASIC_NMOS role=low_side_switch
COMPONENT R_LED2 BASIC_RESISTOR value=220ohm role=current_limit
COMPONENT LED2 LIGHT_LED_RED
COMPONENT R_GATE2 BASIC_RESISTOR value=100ohm role=gate_resistor
COMPONENT R_PULL2 BASIC_RESISTOR value=100kohm role=pulldown
COMPONENT Q2 BASIC_NMOS role=low_side_switch

NET VCC:
    V1.POS
    U1.VDD
    R_LED1.1
    R_LED2.1

NET LED1_ANODE:
    R_LED1.2
    LED1.A

NET LED1_SWITCH:
    LED1.K
    Q1.D

NET CTRL1:
    U1.PA7
    R_GATE1.1

NET GATE1:
    R_GATE1.2
    R_PULL1.1
    Q1.G

NET LED2_ANODE:
    R_LED2.2
    LED2.A

NET LED2_SWITCH:
    LED2.K
    Q2.D

NET CTRL2:
    U1.PA6
    R_GATE2.1

NET GATE2:
    R_GATE2.2
    R_PULL2.1
    Q2.G

NET GND:
    V1.NEG
    U1.GND
    R_PULL1.2
    Q1.S
    R_PULL2.2
    Q2.S
""")
    layout = heuristic_placement_engine().place(circuit, library)
    assert layout.components["Q1"].y != layout.components["Q2"].y
    assert len({(layout.components[ref].x, layout.components[ref].y) for ref in ["Q1", "Q2", "LED1", "LED2", "R_LED1", "R_LED2", "R_GATE1", "R_GATE2", "R_PULL1", "R_PULL2"]}) == 10


def test_repeated_decoupling_and_rc_filters_do_not_stack() -> None:
    circuit, library, _ = analyze_text("""CIRCUIT RepeatedPassives
COMPONENT VIN POWER_DC_SOURCE voltage=5V
COMPONENT U1 MCU_ATtiny402_SOIC8
COMPONENT C1 BASIC_CAPACITOR_CERAMIC value=100nF
COMPONENT C2 BASIC_CAPACITOR_CERAMIC value=1uF
COMPONENT R1 BASIC_RESISTOR value=1kohm
COMPONENT CF1 BASIC_CAPACITOR_CERAMIC value=100nF
COMPONENT R2 BASIC_RESISTOR value=2kohm
COMPONENT CF2 BASIC_CAPACITOR_CERAMIC value=220nF

NET VDD:
    VIN.POS
    U1.VDD
    C1.1
    C2.1
    R1.1
    R2.1

NET A_FILTER:
    R1.2
    CF1.1
    U1.PA6

NET B_FILTER:
    R2.2
    CF2.1
    U1.PA7

NET GND:
    VIN.NEG
    U1.GND
    C1.2
    C2.2
    CF1.2
    CF2.2
""")
    layout = heuristic_placement_engine().place(circuit, library)
    assert (layout.components["C1"].x, layout.components["C1"].y) != (layout.components["C2"].x, layout.components["C2"].y)
    assert (layout.components["R1"].x, layout.components["R1"].y) != (layout.components["R2"].x, layout.components["R2"].y)
    assert (layout.components["CF1"].x, layout.components["CF1"].y) != (layout.components["CF2"].x, layout.components["CF2"].y)
