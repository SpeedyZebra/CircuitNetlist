# Expanded Example Circuits

EX-1 adds a first batch of clean beginner/intermediate examples using MCUs, modules, and IC blocks beyond the original ATtiny-oriented fixtures.

Every circuit listed here is expected to produce zero diagnostics across parse, validation, ERC, placement, routing, canonical scene build, visual DRC, rendered connectivity, SVG export, PNG export when available, and audit manifest comparison.

## Component Scope

The new larger boards are modeled as schematic-level modules with verified subsets of common pins, not full board pinouts:

| Component family | Component ID | Status |
| --- | --- | --- |
| Arduino Nano / ATmega328P module | `MCU_ARDUINO_NANO_ATMEGA328P_MODULE` | module-level abstraction |
| ESP32 DevKit module | `MCU_ESP32_DEVKIT_MODULE` | module-level abstraction |
| Raspberry Pi Pico / RP2040 module | `MCU_RASPBERRY_PI_PICO_RP2040_MODULE` | module-level abstraction |
| LM358 dual op amp | `IC_LM358_DUAL_OP_AMP` | generic verified schematic abstraction |
| LM393 comparator | `IC_LM393_COMPARATOR` | generic verified schematic abstraction |
| MCP3008 SPI ADC | `IC_MCP3008_SPI_ADC` | generic verified schematic abstraction |
| MCP23017 I2C expander | `IC_MCP23017_I2C_GPIO_EXPANDER` | generic verified schematic abstraction |
| LDO regulator block | `POWER_LDO_REGULATOR_BLOCK` | generic schematic block |
| ULN2003 driver array | `IC_ULN2003_DRIVER_ARRAY` | generic verified schematic abstraction |
| L293D motor driver | `IC_L293D_MOTOR_DRIVER` | generic verified schematic abstraction |
| Unidirectional level shifter | `IC_UNIDIRECTIONAL_LEVEL_SHIFTER` | generic schematic block |
| BME280-style I2C sensor module | `MODULE_I2C_SENSOR_BME280` | module-level abstraction |
| DS18B20-style one-wire sensor | `SENSOR_DS18B20_ONEWIRE` | generic verified schematic abstraction |
| WS2812 LED strip input block | `LIGHT_WS2812_STRIP` | module-level abstraction |

Each new component includes metadata for the component information panel: summary, function/common use, pin descriptions, electrical types, pin intents, required pins where useful, package/module type, and `verified_status`.

## New Clean Examples

| Circuit | Path | Demonstrates | Important nets | Supporting passives | Expected diagnostics |
| --- | --- | --- | --- | --- | --- |
| Arduino MOSFET PWM Driver | `test_circuits/good/06_arduino_mosfet_pwm/circuit.cnet` | Arduino PWM driving a low-side N-MOSFET inductive load | `5V`, `PWM`, `GATE`, `LOAD_SW`, `GND` | gate resistor, gate pulldown, flyback diode, local decoupling, bulk capacitor | none |
| ESP32 I2C Sensor | `test_circuits/good/07_esp32_i2c_sensor/circuit.cnet` | 3.3 V ESP32 I2C sensor bus | `5V`, `3V3`, `ESP32_EN`, `I2C_SDA`, `I2C_SCL`, `GND` | SDA/SCL pullups, EN pullup, regulator decoupling, ESP32 decoupling | none |
| Pico SPI ADC | `test_circuits/good/08_pico_spi_adc/circuit.cnet` | RP2040 SPI bus to an external ADC | `3V3`, `ADC_IN`, `SPI_SCLK`, `SPI_MOSI`, `SPI_MISO`, `SPI_CS`, `GND` | input divider, ADC input filter capacitor, ADC decoupling | none |
| LM358 Signal Conditioning | `test_circuits/good/09_lm358_signal_conditioning/circuit.cnet` | Single-supply op-amp front end feeding an MCU ADC | `5V`, `SENSOR_RAW`, `FILTERED_IN`, `AMP_FB`, `AMP_OUT`, `MCU_ADC`, `GND` | input resistor, input filter capacitor, feedback/gain resistors, output resistor, decoupling | none |
| LM393 Comparator | `test_circuits/good/10_lm393_comparator_hysteresis/circuit.cnet` | Open-collector comparator with threshold and hysteresis | `5V`, `REF`, `SENSE`, `COMP_OUT`, `GND` | reference divider, sense divider, hysteresis resistor, output pullup, decoupling | none |
| ULN2003 Relay Driver | `test_circuits/good/11_uln2003_relay_driver/circuit.cnet` | MCU output driving a relay coil through a driver array | `5V`, `12V`, `RELAY_CTRL`, `DRIVER_IN`, `RELAY_LOW`, `GND` | input resistor, flyback diode, logic decoupling | none |
| L293D Motor Driver | `test_circuits/good/12_l293d_motor_driver/circuit.cnet` | H-bridge motor driver with separate logic and motor rails | `5V`, `MOTOR_SUPPLY`, `MOTOR_ENABLE`, `MOTOR_IN1`, `MOTOR_IN2`, `MOTOR_A`, `MOTOR_B`, `GND` | enable pullup, logic decoupling, motor bulk capacitor | none |
| WS2812 Level-Shifted Strip | `test_circuits/good/13_ws2812_level_shifted_led_strip/circuit.cnet` | 3.3 V MCU data level-shifted to a 5 V LED strip | `5V`, `3V3`, `DATA_3V3`, `DATA_5V`, `LED_DIN`, `GND` | series data resistor, logic decoupling, strip bulk capacitor, regulator | none |

## Layout Notes

The audit runner loads an adjacent `circuit.layout.json` when present so saved clean example geometry is honored consistently by the app and audit CLI. The more visually dense EX-1 examples use saved layouts only when needed to avoid clutter, unrelated wire crossings, label/symbol overlaps, and rendered-connectivity mistakes:

- `07_esp32_i2c_sensor`
- `09_lm358_signal_conditioning`
- `10_lm393_comparator_hysteresis`
- `12_l293d_motor_driver`
- `13_ws2812_level_shifted_led_strip`

The saved layouts are still regression artifacts, not hard-coded schematic generation shortcuts. They are loaded through the normal `Layout` model and revalidated by routing, scene construction, visual DRC, rendered connectivity, ERC, and export checks.

## Focused Audit Timing

The EX-1 implementation audit passed all eight new clean examples with zero diagnostics:

| Circuit | Result | Runtime ms | Diagnostics |
| --- | --- | ---: | --- |
| `06_arduino_mosfet_pwm_good` | pass | 8831.439 | none |
| `07_esp32_i2c_sensor_good` | pass | 9399.780 | none |
| `08_pico_spi_adc_good` | pass | 13092.420 | none |
| `09_lm358_signal_conditioning_good` | pass | 980.277 | none |
| `10_lm393_comparator_hysteresis_good` | pass | 4041.818 | none |
| `11_uln2003_relay_driver_good` | pass | 12391.212 | none |
| `12_l293d_motor_driver_good` | pass | 9208.557 | none |
| `13_ws2812_level_shifted_led_strip_good` | pass | 1026.807 | none |

Slowest new circuit in that run: `08_pico_spi_adc_good` at 13092.420 ms.

## Deferred Work

Playwright execution remains deferred for EX-1.

Simulation remains deferred. These examples are schematic, ERC, DRC, rendered-connectivity, and export fixtures; they do not add SPICE, transient simulation, DC solving, or firmware behavior.
