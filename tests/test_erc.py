from pathlib import Path

from circuit_netlist import app as app_module
from circuit_netlist.component_library import load_component_library
from circuit_netlist.erc import ElectricalRuleChecker, run_electrical_rules
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


def codes_for(text: str) -> set[str]:
    return {diag.code for diag in erc_for(text)}


def test_detects_battery_terminal_shorts_in_shared_erc() -> None:
    codes = codes_for("CIRCUIT X\nCOMPONENT B1 POWER_LIPO_1S capacity=1000mAh\nNET BAD:\n B1.POS\n B1.NEG\n")
    assert "ERC_BATTERY_TERMINALS_SHORTED" in codes


def test_validator_no_longer_owns_terminal_short_rules() -> None:
    library = load_component_library(ROOT / "components")
    circuit, _ = NetlistParser().parse_text("CIRCUIT X\nCOMPONENT B1 POWER_LIPO_1S capacity=1000mAh\nNET BAD:\n B1.POS\n B1.NEG\n")
    assert circuit is not None
    diags = CircuitValidator(library).validate(circuit)
    assert not [diag for diag in diags if diag.code and diag.code.startswith("ERC_")]


def test_direct_current_source_terminal_short_is_a_hard_erc_error() -> None:
    codes = codes_for("CIRCUIT X\nCOMPONENT V1 POWER_DC_SOURCE voltage=5V\nNET SHORT:\n V1.POS\n V1.NEG\n")
    assert "ERC_SOURCE_TERMINALS_SHORTED" in codes
    assert "ERC_POWER_GND_SHORT" in codes


def test_led_and_diode_terminal_shorts_are_detected() -> None:
    led_codes = codes_for(
        "CIRCUIT X\n"
        "COMPONENT LED1 LIGHT_LED_RED vf=2V current=10mA\n"
        "COMPONENT R1 BASIC_RESISTOR value=330ohm\n"
        "NET BAD:\n LED1.A\n LED1.K\n R1.1\n"
    )
    diode_codes = codes_for(
        "CIRCUIT X\n"
        "COMPONENT D1 BASIC_DIODE\n"
        "COMPONENT R1 BASIC_RESISTOR value=1kohm\n"
        "NET BAD:\n D1.A\n D1.K\n R1.1\n"
    )
    assert "ERC_LED_TERMINALS_SHORTED" in led_codes
    assert "ERC_DIODE_TERMINALS_SHORTED" in diode_codes


def test_polarized_capacitor_terminal_short_is_detected() -> None:
    codes = codes_for("CIRCUIT X\nCOMPONENT C1 BASIC_CAPACITOR_POLARIZED value=10uF\nNET BAD:\n C1.POS\n C1.NEG\n")
    assert "ERC_CAPACITOR_TERMINALS_SHORTED" in codes


def test_mcu_power_pins_reversed_are_detected() -> None:
    codes = codes_for(
        "CIRCUIT X\n"
        "COMPONENT V1 POWER_DC_SOURCE voltage=5V\n"
        "COMPONENT U1 MCU_ATtiny402_SOIC8\n"
        "NET VCC:\n V1.POS\n U1.GND\n"
        "NET GND:\n V1.NEG\n U1.VDD\n"
    )
    assert "ERC_GROUND_PIN_ON_POSITIVE_RAIL" in codes
    assert "ERC_POSITIVE_SUPPLY_PIN_ON_GROUND" in codes


def test_battery_and_solar_polarity_mistakes_are_detected() -> None:
    battery_codes = codes_for(
        "CIRCUIT X\n"
        "COMPONENT B1 POWER_LIPO_1S capacity=1000mAh\n"
        "COMPONENT R1 BASIC_RESISTOR value=1kohm\n"
        "NET GND:\n B1.POS\n R1.1\n"
        "NET VBAT:\n B1.NEG\n R1.2\n"
    )
    solar_codes = codes_for(
        "CIRCUIT X\n"
        "COMPONENT P1 POWER_SOLAR_PANEL_5V_2W\n"
        "COMPONENT R1 BASIC_RESISTOR value=1kohm\n"
        "NET GND:\n P1.POS\n R1.1\n"
        "NET PANEL_POS:\n P1.NEG\n R1.2\n"
    )
    assert "ERC_POSITIVE_TERMINAL_ON_GROUND" in battery_codes
    assert "ERC_NEGATIVE_TERMINAL_ON_POSITIVE_RAIL" in battery_codes
    assert "ERC_POSITIVE_TERMINAL_ON_GROUND" in solar_codes
    assert "ERC_NEGATIVE_TERMINAL_ON_POSITIVE_RAIL" in solar_codes


def test_555_and_op_amp_supply_reversal_are_detected() -> None:
    timer_codes = codes_for(
        "CIRCUIT X\n"
        "COMPONENT V1 POWER_DC_SOURCE voltage=5V\n"
        "COMPONENT U1 BASIC_555_TIMER\n"
        "NET VCC:\n V1.POS\n U1.GND\n"
        "NET GND:\n V1.NEG\n U1.VCC\n"
    )
    op_amp_codes = codes_for(
        "CIRCUIT X\n"
        "COMPONENT V1 POWER_DC_SOURCE voltage=5V\n"
        "COMPONENT U1 BASIC_OP_AMP\n"
        "NET VCC:\n V1.POS\n U1.V-\n"
        "NET GND:\n V1.NEG\n U1.V+\n"
    )
    assert "ERC_GROUND_PIN_ON_POSITIVE_RAIL" in timer_codes
    assert "ERC_POSITIVE_SUPPLY_PIN_ON_GROUND" in timer_codes
    assert "ERC_SUPPLY_POLARITY_REVERSED" in op_amp_codes
    assert "ERC_POSITIVE_SUPPLY_PIN_ON_GROUND" in op_amp_codes


def test_required_power_and_ground_opens_are_detected() -> None:
    missing_vdd_codes = codes_for(
        "CIRCUIT X\n"
        "COMPONENT V1 POWER_DC_SOURCE voltage=5V\n"
        "COMPONENT U1 MCU_ATtiny402_SOIC8\n"
        "COMPONENT R1 BASIC_RESISTOR value=1kohm\n"
        "NET VCC:\n V1.POS\n R1.1\n"
        "NET GND:\n V1.NEG\n U1.GND\n"
    )
    missing_gnd_codes = codes_for(
        "CIRCUIT X\n"
        "COMPONENT V1 POWER_DC_SOURCE voltage=5V\n"
        "COMPONENT U1 MCU_ATtiny402_SOIC8\n"
        "COMPONENT R1 BASIC_RESISTOR value=1kohm\n"
        "NET VCC:\n V1.POS\n U1.VDD\n"
        "NET GND:\n V1.NEG\n R1.1\n"
    )
    assert "ERC_REQUIRED_PIN_UNCONNECTED" in missing_vdd_codes
    assert "ERC_REQUIRED_PIN_UNCONNECTED" in missing_gnd_codes


def test_power_input_without_source_is_detected_conservatively() -> None:
    codes = codes_for(
        "CIRCUIT X\n"
        "COMPONENT U1 MCU_ATtiny402_SOIC8\n"
        "COMPONENT R1 BASIC_RESISTOR value=1kohm\n"
        "NET VDD:\n U1.VDD\n R1.1\n"
        "NET GND:\n U1.GND\n R1.2\n"
    )
    assert "ERC_POWER_INPUT_NO_SOURCE" in codes


def test_former_regression_divider_checks_are_shared_erc_rules() -> None:
    missing_bottom = codes_for(
        "CIRCUIT X\n"
        "COMPONENT V1 POWER_DC_SOURCE voltage=5V\n"
        "COMPONENT R1 BASIC_RESISTOR value=10kohm role=divider_top\n"
        "NET VIN:\n V1.POS\n R1.1\n"
        "NET MID:\n V1.NEG\n R1.2\n"
    )
    grounded_midpoint = codes_for(
        "CIRCUIT X\n"
        "COMPONENT V1 POWER_DC_SOURCE voltage=5V\n"
        "COMPONENT R1 BASIC_RESISTOR value=10kohm role=divider_top\n"
        "COMPONENT R2 BASIC_RESISTOR value=10kohm role=divider_bottom\n"
        "COMPONENT G1 BASIC_GROUND\n"
        "NET VIN:\n V1.POS\n R1.1\n"
        "NET MID:\n R1.2\n R2.1\n G1.GND\n"
        "NET GND:\n V1.NEG\n R2.2\n"
    )
    assert "ERC_DIVIDER_BOTTOM_MISSING" in missing_bottom
    assert "ERC_DIVIDER_MIDPOINT_GROUNDED" in grounded_midpoint
    assert "ERC_ADC_PIN_UNCONNECTED" in grounded_midpoint


def test_shared_erc_entry_point_matches_compatibility_wrapper() -> None:
    library = load_component_library(ROOT / "components")
    circuit, parse_diags = NetlistParser().parse_text("CIRCUIT X\nCOMPONENT V1 POWER_DC_SOURCE voltage=5V\nNET SHORT:\n V1.POS\n V1.NEG\n")
    assert circuit is not None, parse_diags
    direct = [diag.code for diag in run_electrical_rules(circuit, library)]
    wrapped = [diag.code for diag in ElectricalRuleChecker(library).check(circuit)]
    assert direct == wrapped


def test_app_path_reports_shared_erc_faults() -> None:
    response = app_module.render_loaded_circuit(
        "CIRCUIT X\nCOMPONENT V1 POWER_DC_SOURCE voltage=5V\nNET SHORT:\n V1.POS\n V1.NEG\n",
        "erc_app_path.cnet",
        "uploaded",
        "erc_app_path",
        update_state=False,
    )
    codes = {diag["code"] for diag in response["erc"]}
    assert "ERC_SOURCE_TERMINALS_SHORTED" in codes


def test_regression_module_does_not_define_production_erc_rules() -> None:
    source = (ROOT / "src" / "circuit_netlist" / "regression.py").read_text(encoding="utf-8")
    assert "def extra_regression_checks" not in source
    assert 'code="ERC_' not in source
    assert "run_electrical_rules" in source


def test_audit_and_app_import_shared_erc_engine() -> None:
    audit_source = (ROOT / "src" / "circuit_netlist" / "circuit_audit.py").read_text(encoding="utf-8")
    app_source = (ROOT / "src" / "circuit_netlist" / "app.py").read_text(encoding="utf-8")
    assert "run_electrical_rules" in audit_source
    assert "can_run_electrical_rules" in audit_source
    assert "can_run_electrical_rules" in app_source
    assert 'code="ERC_' not in audit_source


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
