# Circuit Inventory

QA-1 inventory date: 2026-07-03. QA-3 clarifies that "all circuits" means all physical fixture `.cnet` files; embedded unit-test snippets are tracked separately below.

All physical `.cnet` files are classified in `test_circuits/clean_circuits.yaml` or `test_circuits/negative_circuits.yaml`. Clean circuits are expected to produce zero diagnostics across parse, validation, ERC, placement, routing, scene, shared visual DRC, SVG export, and PNG export. Negative circuits are fault fixtures and must produce exactly the listed diagnostic codes.

## Physical Circuit Files

| Circuit | Path | Class | Purpose | Topology family | Expected diagnostics | Status | Manifest | Pytest | Audit CLI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Solar LED Controller | `examples/solar_led.cnet` | clean | Solar charger, LiPo battery, MCU-controlled MOSFET LED load | solar LED / solar charger battery system | none | pass | yes | yes | yes |
| Inverting Op Amp | `examples/inverting_op_amp.cnet` | clean | Inverting amplifier with gain `-R2/R1` | op-amp | none | pass | yes | yes | yes |
| 555 Timer 50% Duty Astable | `examples/555_timer_50_duty_astable.cnet` | clean | 50% duty-cycle astable oscillator | 555 timer | none | pass | yes | yes | yes |
| LED Resistor | `test_circuits/good/01_led_resistor/circuit.cnet` | clean | Simple current-limited LED | LED resistor | none | pass | yes | yes | yes |
| MOSFET Low-Side Switch | `test_circuits/good/02_mosfet_switch/circuit.cnet` | clean | MCU-driven low-side LED switch | MOSFET low-side switch | none | pass | yes | yes | yes |
| Voltage Divider ADC | `test_circuits/good/03_voltage_divider_adc/circuit.cnet` | clean | Divider feeding an MCU ADC input | voltage divider | none | pass | yes | yes | yes |
| Repeated MOSFET 2 Channel | `test_circuits/good/04_repeated_groups/multi_mosfet_2_channel.cnet` | clean | Two independent MOSFET LED channels | repeated MOSFET channels | none | pass | yes | yes | yes |
| Repeated MOSFET 4 Channel | `test_circuits/good/04_repeated_groups/multi_mosfet_4_channel.cnet` | clean | Four independent MOSFET LED channels | repeated MOSFET channels | none | pass | yes | yes | yes |
| Multiple Voltage Dividers | `test_circuits/good/04_repeated_groups/multi_voltage_divider.cnet` | clean | Multiple divider groups sharing rails | voltage divider | none | pass | yes | yes | yes |
| Multiple RC Filters | `test_circuits/good/04_repeated_groups/multi_rc_filter.cnet` | clean | Multiple low-pass RC input filters | RC filter | none | pass | yes | yes | yes |
| Multiple Decoupling Capacitors | `test_circuits/good/04_repeated_groups/multi_decoupling.cnet` | clean | Local and bulk rail capacitors | decoupling | none | pass | yes | yes | yes |
| Decoupling Plus RC Filters | `test_circuits/good/04_repeated_groups/multi_decoupling_with_rc_filters.cnet` | clean | Mixed decoupling bank and RC filters | decoupling + RC | none | pass | yes | yes | yes |
| Relay Flyback Driver | `test_circuits/good/05_relay_flyback/circuit.cnet` | clean | Low-side relay-coil driver with flyback diode | motor or relay with flyback | none | pass | yes | yes | yes |
| Missing LED Resistor | `test_circuits/faults/01_led_resistor/missing_resistor.cnet` | negative | LED intentionally lacks a current-limiting resistor | LED resistor | `ERC_LED_NO_CURRENT_LIMIT` | pass | yes | yes | yes |
| Reversed LED | `test_circuits/faults/01_led_resistor/reversed_led.cnet` | negative | LED polarity intentionally reversed | LED resistor | `ERC_LED_POLARITY_REVERSED` | pass | yes | yes | yes |
| LED Terminals Shorted | `test_circuits/faults/01_led_resistor/led_terminals_shorted.cnet` | negative | LED anode and cathode intentionally tied together | LED resistor | `ERC_LED_TERMINALS_SHORTED` | pass | yes | yes | yes |
| Power Ground Short | `test_circuits/faults/01_led_resistor/power_ground_short.cnet` | negative | Power and ground intentionally shorted | LED resistor | `ERC_POWER_GND_SHORT`, `VALIDATION_PIN_MULTIPLE_NETS` | pass | yes | yes | yes |
| Invalid LED Pin | `test_circuits/faults/01_led_resistor/invalid_pin.cnet` | negative | Netlist intentionally references an unknown LED pin | LED resistor | `VALIDATION_UNKNOWN_PIN` | pass | yes | yes | yes |
| Unknown Component | `test_circuits/faults/01_led_resistor/unknown_component.cnet` | negative | Unknown component type and references | LED resistor | `VALIDATION_UNKNOWN_COMPONENT`, `VALIDATION_UNKNOWN_REF`, `VALIDATION_UNKNOWN_REF` | pass | yes | yes | yes |
| Floating MOSFET Gate | `test_circuits/faults/02_mosfet_switch/floating_gate.cnet` | negative | MOSFET gate intentionally floating | MOSFET low-side switch | `ERC_MOSFET_GATE_FLOATING` | pass | yes | yes | yes |
| Missing MOSFET Pull-Down | `test_circuits/faults/02_mosfet_switch/missing_gate_pulldown.cnet` | negative | MOSFET gate intentionally lacks pull-down | MOSFET low-side switch | `ERC_MOSFET_GATE_NO_PULLDOWN` | pass | yes | yes | yes |
| MOSFET Source Not Ground | `test_circuits/faults/02_mosfet_switch/source_not_ground.cnet` | negative | Low-side NMOS source intentionally tied away from ground | MOSFET low-side switch | `ERC_MOSFET_SOURCE_NOT_GROUND` | pass | yes | yes | yes |
| Divider Midpoint Grounded | `test_circuits/faults/03_voltage_divider_adc/midpoint_shorted_to_ground.cnet` | negative | Divider midpoint intentionally grounded | voltage divider | `ERC_DIVIDER_MIDPOINT_GROUNDED` | pass | yes | yes | yes |
| Divider ADC Unconnected | `test_circuits/faults/03_voltage_divider_adc/adc_unconnected.cnet` | negative | Divider midpoint intentionally lacks ADC/input load | voltage divider | `ERC_ADC_PIN_UNCONNECTED` | pass | yes | yes | yes |
| Divider Missing Bottom Resistor | `test_circuits/faults/03_voltage_divider_adc/missing_bottom_resistor.cnet` | negative | Divider intentionally lacks bottom resistor | voltage divider | `ERC_DIVIDER_BOTTOM_MISSING` | pass | yes | yes | yes |

## Embedded Test Netlists

Embedded snippets are not user-facing project fixtures. They are inventoried separately here so they are not unknown, but they remain local unit-test data unless promoted to physical `.cnet` files and manifests.

| Source | Class | Purpose / family | Manifest | Pytest | Audit CLI |
| --- | --- | --- | --- | --- | --- |
| `tests/test_load_circuit.py::VALID_TEXT` | clean synthetic | Uploaded LED resistor smoke test | no | yes | no |
| `tests/test_load_circuit.py::bad.cnet` | negative synthetic | Uploaded unknown-pin validation fault | no | yes | no |
| `tests/test_erc.py` inline snippets | negative synthetic | Battery terminal short, floating MOSFET gate, LED without resistor | no | yes | no |
| `tests/test_validator.py` inline snippets | negative/clean synthetic | Duplicate refs, unknown component, pin alias and unknown-pin validation | no | yes | no |
| `tests/test_topology.py::RENAMED_SOLAR` | clean synthetic | Solar topology invariant under renamed refs | no | yes | no |
| `tests/test_topology.py::RC_LOWPASS` | clean synthetic | RC filter topology detection | no | yes | no |
| `tests/test_topology.py::PULL_UP` | clean synthetic | Pull-up topology detection | no | yes | no |
| `tests/test_topology.py` role/net/capacitor/flyback/repeated snippets | clean/negative synthetic | Topology role validation, net classification, capacitor classification, flyback diode, repeated groups | no | yes | no |
| `tests/test_constraint_placement.py::low_side_text` | clean synthetic | Low-side MOSFET placement/scoring fixture | no | yes | no |
| `tests/test_constraint_placement.py` rename/reversed LED/multi-controller snippets | clean/negative synthetic | Placement determinism, expected ERC preservation, realistic controller grouping | no | yes | no |
| `tests/test_component_size_envelopes.py` renamed repeated-group text | clean synthetic | Semantic layout equivalence after ref rename | no | yes | no |
| `tests/test_visual_drc_text.py` model-built scenes | negative synthetic scene data | Wire/text DRC collision behavior without `.cnet` parsing | no | yes | no |

## Notes

- Formal audit coverage intentionally targets physical `.cnet` files because these are user-loadable fixtures and examples.
- Embedded snippets are still covered by pytest, but they are unit-level data for targeted parser, validator, topology, DRC, or placement behavior.
- New user-facing circuits should be added as physical `.cnet` files and included in either the clean or negative manifest.
