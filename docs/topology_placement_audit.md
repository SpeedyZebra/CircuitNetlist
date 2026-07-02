# Topology Placement Audit

This audit records the pre-milestone production placement rules that depended on exact component reference designators.

## Production Placement Findings

### `src/circuit_netlist/placement.py`

`DeterministicPlacementEngine.place`

- Called `_place_solar_led_example` before generic placement.
- Classification: avoidable name-based component handling.
- Impact: any equivalent solar LED circuit using names such as `R17`, `R18`, `Q3`, or `MCU7` bypassed the tuned layout and fell back to generic columns.

`DeterministicPlacementEngine._place_solar_led_example`

- Contained exact coordinate map for `PANEL1`, `CHG1`, `BAT1`, `U1`, `C1`, `R_SUN_TOP`, `R_SUN_BOT`, `R_BAT_TOP`, `R_BAT_BOT`, `R_LED`, `LED1`, `R_GATE`, `Q1`, and `R_PULL`.
- Contained exact orientation sets for `R_SUN_TOP`, `R_SUN_BOT`, `R_BAT_TOP`, `R_BAT_BOT`, `R_PULL`, `C1`, `R_GATE`, and `R_LED`.
- Required `{"PANEL1", "CHG1", "BAT1", "U1", "Q1", "LED1"}` to decide whether the special placement applied.
- Classification: avoidable name-based component handling.

`DeterministicPlacementEngine._choose_two_pin_orientations`

- Called helper functions that classified components by exact references.
- Classification: avoidable name-based component handling.

`is_voltage_divider_ref`

- Returned true only for `R_SUN_TOP`, `R_SUN_BOT`, `R_BAT_TOP`, `R_BAT_BOT`.
- Classification: avoidable name-based component handling.

`is_pull_resistor_ref`

- Returned true only for `R_PULL`.
- Classification: avoidable name-based component handling.

`is_decoupling_capacitor_ref`

- Returned true only for `C1`.
- Classification: avoidable name-based component handling.

`is_series_element_ref`

- Returned true only for `R_GATE` and `R_LED`.
- Classification: avoidable name-based component handling.

`DeterministicPlacementEngine._bucket`

- Used component IDs and categories, plus `ref.upper().startswith("GND")`.
- Classification: mixed. Component ID/category handling is legitimate semantic placement. The `GND` reference-prefix check was avoidable reference-name handling and was removed from production placement.

## Production Router Findings

### `src/circuit_netlist/router.py`

- Uses well-known net names such as `GND`, `VBAT`, `VDD`, `VCC`, and `PANEL_POS`.
- Classification: legitimate semantic net handling.
- No exact solar example component-reference placement hacks were found.

## Production Renderer Findings

### `src/circuit_netlist/renderer.py`

- Uses component refs only for SVG IDs, labels, endpoint lookup, and deterministic rendering.
- Classification: legitimate display and saved-layout behavior.
- No exact solar example component-reference placement hacks were found.

## Regression/Test Findings

### `tests/test_renderer.py`

- Contains solar example references in fixture expectations.
- Classification: temporary regression fixture.
- These tests should evolve toward relative topology expectations, but test fixtures may still mention concrete refs.

## Result

Milestone 1 removes the production solar coordinate map and exact-reference orientation helpers. Replacement behavior consumes `TopologyAnalysis`, which detects component roles and circuit patterns from component metadata, pin semantics, net semantics, and connectivity.
