from pathlib import Path

from circuit_netlist.component_library import load_component_library
from circuit_netlist.parser import NetlistParser
from circuit_netlist.validator import CircuitValidator


ROOT = Path(__file__).resolve().parents[1]


def diagnostics_for(text: str):
    circuit, parse_diags = NetlistParser().parse_text(text)
    assert circuit is not None, parse_diags
    return CircuitValidator(load_component_library(ROOT / "components")).validate(circuit)


def test_duplicate_component_references() -> None:
    diags = diagnostics_for("CIRCUIT X\nCOMPONENT R1 BASIC_RESISTOR value=1kohm\nCOMPONENT R1 BASIC_RESISTOR value=2kohm\n")
    assert any("Duplicate reference" in d.message for d in diags)


def test_unknown_component_type() -> None:
    diags = diagnostics_for("CIRCUIT X\nCOMPONENT U1 MCU_ATTiny402\n")
    assert any("Unknown component type" in d.message and d.suggestions for d in diags)


def test_unknown_pin_and_alias_resolution() -> None:
    ok = diagnostics_for("CIRCUIT X\nCOMPONENT U1 MCU_ATtiny402_SOIC8\nCOMPONENT C1 BASIC_CAPACITOR_CERAMIC value=1uF\nNET V:\n U1.VCC\n C1.1\n")
    assert not any("does not exist" in d.message for d in ok)
    bad = diagnostics_for("CIRCUIT X\nCOMPONENT U1 MCU_ATtiny402_SOIC8\nCOMPONENT C1 BASIC_CAPACITOR_CERAMIC value=1uF\nNET V:\n U1.PA8\n C1.1\n")
    assert any("does not exist" in d.message for d in bad)
