# Electrical Rule Checking

ERC-1 consolidates production electrical checks in `src/circuit_netlist/erc.py`.

The shared entry point is:

```python
run_electrical_rules(circuit, library, topology=None, options=None)
```

`ElectricalRuleChecker.check()` remains as a compatibility wrapper and delegates to the same function. The app, regression runner, circuit audit, placement/debug paths, and tests should use this shared engine rather than defining local `ERC_` rules.

## Validator, ERC, Visual DRC, And Rendered Connectivity

The validator resolves syntax and library references. It owns structural netlist errors such as unknown components, unknown pins, duplicate references, single-pin net validation, and the same physical pin assigned to multiple nets.

ERC owns electrical intent checks after references are resolvable. It detects shorts, reversed supply/polarity cases, missing required pins, floating control inputs, MOSFET gate issues, LED current-limit issues, and topology-aware electrical checks such as divider midpoint faults.

Visual DRC owns schematic geometry. It detects component overlap, wire/text/symbol/label overlap, unrelated collinear wire overlap, and unrelated wire intersections with no explicit same-net junction.

Rendered connectivity owns scene-level electrical continuity. It verifies that rendered pins, wires, stubs, labels, power symbols, ground symbols, and junctions connect the same nets as the parsed circuit. It reports rendered opens, rendered shorts, missing pin contacts, dangling stubs, label/symbol disconnects, and wrong-net scene contacts.

## Pin Intent

Component-library pins may declare optional `intent` metadata in addition to `electrical_type`. Examples include:

- `source_positive`, `source_negative`
- `battery_positive`, `battery_negative`
- `solar_positive`, `solar_negative`
- `supply_positive`, `supply_ground`
- `charger_input`, `charger_battery`
- `regulator_input`, `regulator_output`
- `op_amp_positive_supply`, `op_amp_negative_supply`
- `mosfet_gate`, `mosfet_drain`, `mosfet_source`
- `diode_anode`, `diode_cathode`
- `led_anode`, `led_cathode`
- `reset_pin`, `control_pin`, `timing_pin`

ERC uses this metadata to classify nets and diagnose polarity mistakes without relying only on reference names.

## Rail Classification

Known positive rails include names such as `VCC`, `VDD`, `VBAT`, `VIN`, `VOUT`, `SYS`, `PANEL_POS`, `5V`, `3V3`, and `12V`.

Known ground rails include `GND`, `AGND`, `DGND`, `PGND`, `0V`, and `VSS`.

Pin intent also contributes to classification. For example, a net containing a `supply_ground` pin is treated as ground, and a net containing a `battery_positive` pin is treated as a positive rail. Negative DC supply sources are handled specially: a `POWER_DC_SOURCE` with negative voltage or `role=negative_supply` treats `POS` as the ground reference and `NEG` as the negative rail.

## Short Detection

ERC detects direct terminal shorts for polarity-sensitive devices:

- DC source `POS`/`NEG`
- battery `POS`/`NEG`
- solar panel `POS`/`NEG`
- LED `A`/`K`
- diode `A`/`K`
- polarized capacitor `POS`/`NEG`
- supply pairs such as IC `VCC`/`GND` or op amp `V+`/`V-`
- charger/regulator critical pins against ground

ERC also diagnoses nets classified as both positive rail and ground using `ERC_POWER_GND_SHORT` and `ERC_SUPPLY_NET_CONFLICT`.

## Polarity Detection

ERC diagnoses obvious rail mistakes, including:

- ground pins on positive rails
- positive supply pins on ground
- source, battery, or solar positive terminals on ground
- source, battery, or solar negative terminals on positive rails
- op amp negative supply on a positive rail

Representative codes include `ERC_GROUND_PIN_ON_POSITIVE_RAIL`, `ERC_POSITIVE_SUPPLY_PIN_ON_GROUND`, `ERC_POSITIVE_TERMINAL_ON_GROUND`, `ERC_NEGATIVE_TERMINAL_ON_POSITIVE_RAIL`, and `ERC_SUPPLY_POLARITY_REVERSED`.

## Open Detection

ERC checks required power and polarity pins using component metadata. Missing required rails produce `ERC_REQUIRED_PIN_UNCONNECTED`.

Control and reset pins can produce `ERC_CONTROL_PIN_FLOATING` when required and not connected. MOSFET gates still produce `ERC_MOSFET_GATE_FLOATING` when unconnected and `ERC_MOSFET_GATE_NO_PULLDOWN` when no bias path is found.

Named positive rails with power-input pins and no source produce `ERC_POWER_INPUT_NO_SOURCE`.

## Topology-Aware Rules

Former regression-only checks now live in the shared ERC engine:

- `ERC_DIVIDER_MIDPOINT_GROUNDED`
- `ERC_ADC_PIN_UNCONNECTED`
- `ERC_DIVIDER_BOTTOM_MISSING`
- `ERC_MOSFET_GATE_TIED_TO_POWER`

This keeps app, regression, audit, and tests aligned.

## Running After Validation

ERC normally runs after validation succeeds. A narrow exception allows ERC to run when the only blocking validation error is `VALIDATION_PIN_MULTIPLE_NETS`, because pin references are still resolvable and ERC can report the electrical short caused by assigning one physical pin to two nets.

Unknown components and unknown pins still block ERC.

## Adding A Rule

1. Add or reuse pin intent metadata in the component library.
2. Implement the rule in `src/circuit_netlist/erc.py`.
3. Return normalized `Diagnostic` objects with stable `ERC_` codes and useful component, pin, net, and subsystem metadata.
4. Add direct unit tests in `tests/test_erc.py`.
5. Add a physical negative fixture when the behavior should be part of the quality gate.
6. Run the fast tests, full pytest, regression, and circuit audit.

## Adding A Negative Fixture

1. Place the `.cnet` file under `test_circuits/faults/`.
2. Add an entry to `test_circuits/negative_circuits.yaml`.
3. List the exact diagnostic code multiset.
4. Avoid unrelated DRC, placement, routing, or export noise.
5. Run `python -m circuit_netlist.circuit_audit --case <id>`.

## Known Limits

ERC is conservative and schematic-level. Rendered connectivity is conservative and scene-level. Neither simulates voltage, current, timing, battery state, solar conditions, or op amp stability. ERC catches obvious open/short/polarity mistakes from net topology and component metadata; rendered connectivity catches mismatches between the intended netlist and the actual drawn scene. They are not SPICE replacements.
