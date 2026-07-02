from __future__ import annotations

from pathlib import Path

from circuit_netlist.component_library import load_component_library
from circuit_netlist.models import Layout, Placement
from circuit_netlist.parser import NetlistParser
from circuit_netlist.placement import DeterministicPlacementEngine
from circuit_netlist.topology import ComponentRole, PatternType, TopologyAnalyzer


ROOT = Path(__file__).resolve().parents[1]


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


def test_flyback_detection_is_available_but_library_has_no_diode_component() -> None:
    _, _, analysis = analyze_text("CIRCUIT NoFlyback\nCOMPONENT L1 BASIC_INDUCTOR value=10mH\nNET A:\n L1.1\nNET B:\n L1.2\n")
    assert patterns(analysis, PatternType.FLYBACK_DIODE) == []


def test_renamed_solar_layout_preserves_relative_topology() -> None:
    circuit, library, analysis = analyze_text(RENAMED_SOLAR)
    layout = DeterministicPlacementEngine().place(circuit, library)
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
    layout1 = DeterministicPlacementEngine().place(circuit, library)
    layout2 = DeterministicPlacementEngine().place(circuit, library)
    assert layout1.model_dump(mode="json") == layout2.model_dump(mode="json")
    existing = Layout(components={"Q12": Placement(x=321, y=654, rotation=90, locked=True)})
    layout3 = DeterministicPlacementEngine().place(circuit, library, existing)
    assert layout3.components["Q12"] == Placement(x=321, y=654, rotation=90, locked=True)


def test_production_placement_has_no_known_solar_reference_hacks() -> None:
    placement_source = (ROOT / "src" / "circuit_netlist" / "placement.py").read_text(encoding="utf-8")
    forbidden = ["R_SUN_TOP", "R_SUN_BOT", "R_BAT_TOP", "R_BAT_BOT", "R_PULL", "R_GATE", "R_LED"]
    assert not [token for token in forbidden if token in placement_source]
