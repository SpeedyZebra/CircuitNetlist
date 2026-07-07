from pathlib import Path

import yaml

from circuit_netlist import app as app_module
from circuit_netlist.component_library import load_component_library
from circuit_netlist.models import ElectricalType, Layout, PinIntent
from circuit_netlist.parser import NetlistParser


ROOT = Path(__file__).resolve().parents[1]
NEW_CIRCUIT_IDS = {
    "06_arduino_mosfet_pwm_good": "test_circuits/good/06_arduino_mosfet_pwm/circuit.cnet",
    "07_esp32_i2c_sensor_good": "test_circuits/good/07_esp32_i2c_sensor/circuit.cnet",
    "08_pico_spi_adc_good": "test_circuits/good/08_pico_spi_adc/circuit.cnet",
    "09_lm358_signal_conditioning_good": "test_circuits/good/09_lm358_signal_conditioning/circuit.cnet",
    "10_lm393_comparator_hysteresis_good": "test_circuits/good/10_lm393_comparator_hysteresis/circuit.cnet",
    "11_uln2003_relay_driver_good": "test_circuits/good/11_uln2003_relay_driver/circuit.cnet",
    "12_l293d_motor_driver_good": "test_circuits/good/12_l293d_motor_driver/circuit.cnet",
    "13_ws2812_level_shifted_led_strip_good": "test_circuits/good/13_ws2812_level_shifted_led_strip/circuit.cnet",
}
NEW_COMPONENTS = [
    "MCU_ARDUINO_NANO_ATMEGA328P_MODULE",
    "MCU_ESP32_DEVKIT_MODULE",
    "MCU_RASPBERRY_PI_PICO_RP2040_MODULE",
    "IC_LM358_DUAL_OP_AMP",
    "IC_LM393_COMPARATOR",
    "IC_MCP3008_SPI_ADC",
    "IC_MCP23017_I2C_GPIO_EXPANDER",
    "POWER_LDO_REGULATOR_BLOCK",
    "IC_ULN2003_DRIVER_ARRAY",
    "IC_L293D_MOTOR_DRIVER",
    "IC_UNIDIRECTIONAL_LEVEL_SHIFTER",
    "MODULE_I2C_SENSOR_BME280",
    "SENSOR_DS18B20_ONEWIRE",
    "LIGHT_WS2812_STRIP",
]
SAVED_LAYOUT_CASE_IDS = [
    "07_esp32_i2c_sensor_good",
    "09_lm358_signal_conditioning_good",
    "10_lm393_comparator_hysteresis_good",
    "12_l293d_motor_driver_good",
    "13_ws2812_level_shifted_led_strip_good",
]


def clean_manifest() -> dict:
    return yaml.safe_load((ROOT / "test_circuits" / "clean_circuits.yaml").read_text(encoding="utf-8"))


def expected_results() -> dict:
    return yaml.safe_load((ROOT / "test_circuits" / "expected" / "expected_results.yaml").read_text(encoding="utf-8"))["circuits"]


def parse_case(relative_path: str):
    circuit, diagnostics = NetlistParser().parse_file(ROOT / relative_path)
    assert circuit is not None, diagnostics
    assert diagnostics == []
    return circuit


def net_pins(circuit) -> dict[str, set[tuple[str, str]]]:
    return {net.name: {(pin.component_ref, pin.pin_name) for pin in net.pins} for net in circuit.nets}


def test_ex1_clean_circuits_are_manifested_without_expected_diagnostics() -> None:
    manifest_by_id = {item["id"]: item for item in clean_manifest()["circuits"]}
    expected = expected_results()
    for case_id, path in NEW_CIRCUIT_IDS.items():
        assert manifest_by_id[case_id]["path"] == path
        assert manifest_by_id[case_id].get("expected_diagnostics", []) == []
        assert expected[path.removeprefix("test_circuits/")]["expected_codes"] == []


def test_ex1_complex_examples_have_valid_saved_layouts_for_audit_geometry() -> None:
    for case_id in SAVED_LAYOUT_CASE_IDS:
        layout_path = (ROOT / NEW_CIRCUIT_IDS[case_id]).with_suffix(".layout.json")
        assert layout_path.exists(), case_id
        layout = Layout.model_validate_json(layout_path.read_text(encoding="utf-8"))
        assert layout.components, case_id


def test_ex1_components_have_component_info_metadata() -> None:
    library = load_component_library(ROOT / "components")
    assert not library.diagnostics
    for component_id in NEW_COMPONENTS:
        component = library.get(component_id)
        assert component is not None, component_id
        metadata = component.metadata
        assert metadata.get("verified_status"), component_id
        assert metadata.get("summary"), component_id
        assert metadata.get("function") or metadata.get("common_use"), component_id
        pin_descriptions = metadata.get("pins", {})
        assert pin_descriptions, component_id
        for pin in component.pins:
            assert pin_descriptions.get(pin.name, {}).get("description"), f"{component_id}.{pin.name}"
            assert pin.intent is not None, f"{component_id}.{pin.name}"


def test_new_mcu_modules_have_required_power_and_ground_pins() -> None:
    library = load_component_library(ROOT / "components")
    for component_id in [
        "MCU_ARDUINO_NANO_ATMEGA328P_MODULE",
        "MCU_ESP32_DEVKIT_MODULE",
        "MCU_RASPBERRY_PI_PICO_RP2040_MODULE",
    ]:
        component = library.get(component_id)
        assert component is not None
        required_power = {pin.intent for pin in component.pins if pin.required and pin.electrical_type == ElectricalType.power_in}
        assert PinIntent.supply_positive in required_power
        assert PinIntent.supply_ground in required_power
        assert component.metadata["verified_status"] == "module_level_abstraction"


def test_i2c_sensor_circuit_has_external_pullups() -> None:
    pins = net_pins(parse_case(NEW_CIRCUIT_IDS["07_esp32_i2c_sensor_good"]))
    assert {("R_SDA", "1"), ("R_SCL", "1")} <= pins["3V3"]
    assert {("U1", "GPIO21_SDA"), ("SENS1", "SDA"), ("R_SDA", "2")} <= pins["I2C_SDA"]
    assert {("U1", "GPIO22_SCL"), ("SENS1", "SCL"), ("R_SCL", "2")} <= pins["I2C_SCL"]


def test_comparator_output_has_pullup_and_hysteresis_feedback() -> None:
    pins = net_pins(parse_case(NEW_CIRCUIT_IDS["10_lm393_comparator_hysteresis_good"]))
    assert ("R_OUT_PULL", "1") in pins["5V"]
    assert {("U2", "OUTA"), ("R_OUT_PULL", "2"), ("R_HYST", "2"), ("U1", "D5_PWM")} <= pins["COMP_OUT"]
    assert {("U2", "INA-"), ("R_HYST", "1")} <= pins["REF"]


def test_mosfet_driver_has_gate_resistor_pulldown_and_flyback() -> None:
    pins = net_pins(parse_case(NEW_CIRCUIT_IDS["06_arduino_mosfet_pwm_good"]))
    assert {("R_GATE", "2"), ("R_PULL", "1"), ("Q1", "G")} <= pins["GATE"]
    assert {("R_PULL", "2"), ("Q1", "S")} <= pins["GND"]
    assert {("LOAD1", "1"), ("D1", "K")} <= pins["5V"]
    assert {("LOAD1", "2"), ("D1", "A"), ("Q1", "D")} <= pins["LOAD_SW"]


def test_motor_driver_has_logic_motor_supplies_enable_and_motor_outputs() -> None:
    pins = net_pins(parse_case(NEW_CIRCUIT_IDS["12_l293d_motor_driver_good"]))
    assert {("U2", "VLOGIC"), ("U1", "5V"), ("R_EN", "1")} <= pins["5V"]
    assert {("U2", "VMOTOR"), ("C_BULK", "POS")} <= pins["MOTOR_SUPPLY"]
    assert {("U2", "EN1"), ("R_EN", "2")} <= pins["MOTOR_ENABLE"]
    assert {("U2", "OUT1"), ("MOTOR1", "1")} <= pins["MOTOR_A"]
    assert {("U2", "OUT2"), ("MOTOR1", "2")} <= pins["MOTOR_B"]


def test_ws2812_circuit_has_level_shifter_bulk_cap_and_series_resistor() -> None:
    pins = net_pins(parse_case(NEW_CIRCUIT_IDS["13_ws2812_level_shifted_led_strip_good"]))
    assert {("U2", "VCCB"), ("LEDS1", "5V"), ("C_BULK", "POS")} <= pins["5V"]
    assert {("UREG1", "VOUT"), ("U1", "3V3"), ("U2", "VCCA")} <= pins["3V3"]
    assert {("U1", "GP15"), ("U2", "A_IN")} <= pins["DATA_3V3"]
    assert {("U2", "B_OUT"), ("R_DATA", "1")} <= pins["DATA_5V"]
    assert {("R_DATA", "2"), ("LEDS1", "DIN")} <= pins["LED_DIN"]


def test_load_circuit_catalog_includes_ex1_friendly_names() -> None:
    good_titles = {item["title"] for item in app_module.get_circuits()["regression"]["good"]}
    assert {
        "Arduino MOSFET PWM Driver",
        "ESP32 I2C Sensor",
        "Pico SPI ADC",
        "LM358 Signal Conditioning",
        "LM393 Comparator",
        "ULN2003 Relay Driver",
        "L293D Motor Driver",
        "WS2812 Level-Shifted Strip",
    } <= good_titles
