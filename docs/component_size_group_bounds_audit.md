# Component Size Group Bounds Audit

Milestone 1.2 added functional group envelopes, but several group sizes were still fixed estimates. Those estimates worked for the production component library but did not expand when resistor, LED, MOSFET, capacitor, or controller body dimensions changed.

## Hardcoded Group Dimensions

`src/circuit_netlist/placement.py`

- `_place_led_current_limits`
  - Used `_pattern_bounds(pattern, width=640, height=260)`.
  - Assumed standard 140x80 resistors and 140x100 LEDs.
  - Did not calculate the body union of the resistor and LED.

- `_place_gate_resistors`
  - Used `_pattern_bounds(pattern, width=780, height=300)`.
  - Assumed a standard gate resistor and MOSFET spacing.
  - Did not expand for larger gate resistor or MOSFET bodies.

- `_place_low_side_switches`
  - Used `_low_side_bounds` with `max_x=720` and `max_y=460`.
  - Assumed standard LED, resistor, MOSFET, and pull-down sizes.
  - Component positions were fixed offsets from the MOSFET position.

- `_place_voltage_dividers`
  - Used `_pattern_bounds(pattern, width=120, height=420, margin_x=80, margin_y=80)`.
  - Assumed vertical standard resistor dimensions and fixed high/mid/low spacing.

- `_place_rc_filters`
  - Used `_pattern_bounds(pattern, width=520, height=260)`.
  - Assumed fixed resistor-to-capacitor spacing.

- `_place_decoupling`
  - Used fixed capacitor slot spacing near the target component.
  - Considered target body height but not actual capacitor widths or heights.

## Fixed Lane Spacing

`GROUP_CLEARANCE_X` and `GROUP_CLEARANCE_Y` are legitimate inter-group clearances, but previous lane increments were based on fixed pattern bounds rather than actual member bodies.

## Orientation Handling

The project already has the correct orientation convention in `geometry.component_size`. A 90 or 270 degree rotation swaps width and height for orientation-sensitive two-pin parts. The previous group-bound code did not use this helper when calculating the group envelope.

## Pin, Routing, Text, and Symbol Margins

The 1.2 implementation included conservative constants:

- `PIN_ESCAPE_ALLOWANCE`
- `TEXT_CLEARANCE_ALLOWANCE`
- `POWER_SYMBOL_CLEARANCE`
- Pattern-specific routing margins

Those margins are still useful, but they must expand an actual body union rather than substitute for it.

## Why Oversized Symbols Escaped

When test variants enlarged resistor and LED bodies, the local members still landed at fixed offsets. Since the lane envelope was unchanged, the next channel began before the previous channel's larger bodies, symbols, text, and routing corridors had cleared. This produced component overlaps and wire/symbol overlaps in repeated MOSFET channels and mixed passive groups.

Milestone 1.3 changes the primary group-size calculation to:

1. Generate local member positions and orientations.
2. Calculate each member's oriented body bounds using `component_size`.
3. Union those body bounds.
4. Expand the union by conservative routing, escape, text, and power/ground margins.
5. Allocate lanes using the calculated envelope width or height.
