# Component-Size-Aware Group Envelopes

Milestone 1.3 keeps the existing topology-driven placement heuristics but changes how repeated functional-group spacing is calculated.

## Why Fixed Bounds Were Insufficient

Earlier repeated-group placement used fixed dimensions such as `width=520, height=260` for RC filters or `max_x=720, max_y=460` for low-side MOSFET channels. Those estimates matched the current production symbol sizes, but they did not expand when a test library used larger resistors, LEDs, MOSFETs, capacitors, or controllers.

## Oriented Body Bounds

Placement now uses the same orientation convention as rendering:

- `geometry.component_size(definition, placement)` returns the effective body size.
- For orientation-sensitive two-pin parts, 90 and 270 degrees swap width and height.
- 0 and 180 degrees preserve body dimensions.
- If geometry is unavailable or invalid, placement uses a conservative fallback size and records that fallback in envelope debug data.

## Member Union

Each repeated group first creates a local arrangement of its members. For each member, placement calculates oriented body bounds at the proposed local coordinates. The group body union is:

```text
body_min_x = min(member.min_x)
body_min_y = min(member.min_y)
body_max_x = max(member.max_x)
body_max_y = max(member.max_y)
```

The expanded envelope adds routing, pin escape, text, and local power/ground-symbol margins around that body union.

## Lane Allocation

Lane spacing uses calculated envelope dimensions:

```text
next_origin_y = current_origin_y + group.height + GROUP_CLEARANCE_Y
next_origin_x = current_origin_x + group.width + GROUP_CLEARANCE_X
```

Groups with larger members therefore consume larger lanes. Different-sized groups in the same repeated family no longer assume a shared fixed offset.

## Pattern Arrangements

The readable local arrangements from Milestone 1.2 are preserved:

- Low-side MOSFET channels: LED branch above drain, gate resistor aligned with gate, pull-down below gate.
- Voltage dividers: vertical high/mid/low resistor stack.
- RC filters: horizontal series resistor with vertical shunt capacitor.
- Decoupling banks: decoupling/bypass slots near target, bulk/reservoir slots in a separate rail bank.

## Debug Data

`DeterministicPlacementEngine.last_group_envelopes` stores JSON-compatible records with group id, pattern type, member local positions, orientations, oriented bounds, body union, margins, expanded envelope, assigned lane, final origin, and fallback-geometry status. `layout.canvas["functional_groups"]` also receives this information for inspection.

## Locked Layouts

Locked components keep their position and orientation. If a locked MOSFET participates in a low-side channel, nearby automatic members are arranged around the locked MOSFET instead of moving it.

## Current Limitations

These envelopes remain approximate placement heuristics. They reserve conservative routing and symbol space but do not derive final routed geometry. The future Milestone 2 canonical scene model can replace this with richer shape ownership, text geometry, and routing-aware placement.
