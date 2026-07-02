from pathlib import Path

from circuit_netlist.component_library import load_component_library
from circuit_netlist.erc import ElectricalRuleChecker
from circuit_netlist.parser import NetlistParser
from circuit_netlist.validator import CircuitValidator


ROOT = Path(__file__).resolve().parents[1]


def erc_for(text: str):
    library = load_component_library(ROOT / "components")
    circuit, parse_diags = NetlistParser().parse_text(text)
    assert circuit is not None, parse_diags
    validation = CircuitValidator(library).validate(circuit)
    assert not [d for d in validation if d.severity.value in {"ERROR", "FATAL"}], validation
    return ElectricalRuleChecker(library).check(circuit)


def test_detects_battery_terminal_shorts_in_validation() -> None:
    library = load_component_library(ROOT / "components")
    circuit, _ = NetlistParser().parse_text("CIRCUIT X\nCOMPONENT B1 POWER_LIPO_1S capacity=1000mAh\nNET BAD:\n B1.POS\n B1.NEG\n")
    assert circuit is not None
    diags = CircuitValidator(library).validate(circuit)
    assert any("battery terminals are shorted" in d.message for d in diags)


def test_detects_floating_mosfet_gate() -> None:
    diags = erc_for("CIRCUIT X\nCOMPONENT Q1 BASIC_NMOS\nCOMPONENT R1 BASIC_RESISTOR value=1kohm\nNET D:\n Q1.D\n R1.1\nNET S:\n Q1.S\n R1.2\n")
    assert any("gate is floating" in d.message for d in diags)


def test_detects_led_without_current_limiting() -> None:
    diags = erc_for("CIRCUIT X\nCOMPONENT L1 LIGHT_LED_WHITE\nCOMPONENT B1 POWER_LIPO_1S capacity=1000mAh\nNET A:\n B1.POS\n L1.A\nNET K:\n B1.NEG\n L1.K\n")
    assert any("current-limiting" in d.message for d in diags)


def test_ground_does_not_need_apparent_power_source() -> None:
    library = load_component_library(ROOT / "components")
    circuit, parse_diags = NetlistParser().parse_file(ROOT / "examples" / "solar_led.cnet")
    assert circuit is not None, parse_diags
    validation = CircuitValidator(library).validate(circuit)
    assert not [d for d in validation if d.severity.value in {"ERROR", "FATAL"}], validation
    diags = ElectricalRuleChecker(library).check(circuit)
    assert not any("GND has no apparent source" in d.message or "on GND has no apparent source" in d.message for d in diags)
