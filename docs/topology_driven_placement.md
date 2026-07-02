# Topology-Driven Placement

Reference-name-based placement is fragile because equivalent circuits can use different designators. A voltage divider named `R_SUN_TOP` and `R_SUN_BOT` should be placed the same way as an identical divider named `R17` and `R42`.

Milestone 1 adds a lightweight topology-analysis layer in `src/circuit_netlist/topology.py`. The analyzer does not place components directly. It infers reusable facts that placement, debugging tools, and later milestones can consume.

## Information Sources

Topology inference uses these sources, in order:

1. Explicit component `role` parameters.
2. Component-library metadata and category.
3. Renderer and component IDs.
4. Pin electrical types.
5. Pin semantic names such as `GND`, `VCC`, `BAT`, `G`, `A`, and `K`.
6. Circuit graph connectivity.
7. Well-known power and ground net names.
8. Reference names only for deterministic sorting and tie-breaking.

## Component Roles

The analyzer currently infers:

- `source`: DC sources, solar panels, and power-output components.
- `storage`: LiPo/Li-ion battery components.
- `charger`: charger/power-management blocks with charger metadata or `VIN`/`BAT` pins.
- `regulator`: explicit regulator role annotations.
- `controller`: MCUs, op amps, and 555 timers.
- `sensor`: explicit sensor role annotations.
- `switch`: NMOS and PMOS symbols.
- `load`: LEDs and lighting components.
- `protection`: explicit protection role annotations.
- `connector`: explicit connector role annotations.
- `passive`: resistors, capacitors, polarized capacitors, and inductors.
- `unknown`: components without enough information.

Explicit `role=` parameters take precedence over library-derived inference when they map to a supported `ComponentRole`. Local schematic intent roles such as `current_limit`, `gate_resistor`, `divider_top`, `pulldown`, `timing`, and `feedback` remain valid annotations and map to the closest broad role. Unknown role text emits `VALIDATION_UNKNOWN_ROLE` so typos are visible.

## Net Classification

Ground classification is ground-first. `0V`, `GND`, `DGND`, `AGND`, `PGND`, `SGND`, `CHASSIS_GND`, and `VSS` are ground nets, not power nets.

Power classification is intentionally conservative. Common rails such as `VCC`, `VDD`, `VBAT`, `VIN`, `3V3`, `5V`, `12V`, `+5V`, and `-12V` are power nets. Sense nets such as `ADC_5V_SENSE` and `MOTOR_12V_SENSE` are not treated as power rails just because they contain a voltage-like substring.

## Capacitor Roles

Power-to-ground capacitors are classified before decoupling patterns are emitted:

- `decoupling`: small local IC rail capacitors.
- `bypass`: moderate local IC rail capacitors.
- `bulk`: larger rail shunt capacitors.
- `reservoir`: very large capacitors near a source or storage rail.
- `filter`: explicit filter role annotations.
- `unknown_power_shunt`: power shunts with missing, malformed, or ambiguous value data.

Only high-confidence `decoupling` and `bypass` capacitors become `DECOUPLING_CAPACITOR` placement patterns. Bulk and reservoir capacitors remain classified but are not forced into IC-local decoupling placement.

## Pattern Detectors

Implemented detectors:

- Voltage divider
- Pull-up resistor
- Pull-down resistor
- Decoupling capacitor
- Series MOSFET gate resistor
- LED current-limit resistor
- Low-side MOSFET switch group
- RC low-pass filter
- Flyback diode hook, active when diode components exist in the library
- Generic series element

## Confidence

Pattern confidence follows the milestone scale:

- `1.0`: explicit role or unambiguous topology.
- `0.8` to `0.92`: strong topology inference.
- `0.5` to `0.75`: plausible but ambiguous.

Placement only applies pattern-specific rules when confidence is at least `MIN_PLACEMENT_PATTERN_CONFIDENCE`, currently `0.75`.

## Placement Rules

`DeterministicPlacementEngine` now consumes `TopologyAnalysis`:

- Functional roles map to stable left-to-right zones.
- Voltage-divider resistors are vertical and stacked top-to-bottom by high/mid/low net topology.
- Pull-up and pull-down resistors are vertical.
- Decoupling capacitors are vertical and placed near the inferred target IC/controller.
- Gate resistors are horizontal between controller and MOSFET gate.
- LED current-limit resistors are horizontal in the load branch.
- Low-side MOSFET switches group MOSFET, gate resistor, pull-down, and LED branch.
- RC low-pass filters keep the resistor horizontal and capacitor vertical.
- Repeated topology groups allocate deterministic rows or slots instead of stacking at identical coordinates. This covers repeated LED/MOSFET switch channels, multiple decouplers for the same target, repeated dividers, and repeated RC filters.

Milestone 1.2 makes repeated placement dimension-aware without introducing the future canonical scene model. `placement.py` now uses a local `FunctionalGroupBounds` envelope for repeated groups. The envelope includes conservative allowances for oriented component bodies, pin escape distance, text clearance, power/ground symbol clearance, and routing channels. Lane spacing is derived from the previous group's approximate bounds plus `GROUP_CLEARANCE_X` or `GROUP_CLEARANCE_Y`, rather than a single center-to-center offset.

Current repeated-group behavior:

- Low-side MOSFET/LED channels are stacked as complete branch envelopes. The LED current-limit branch sits above the MOSFET drain, the gate resistor aligns with the MOSFET gate, and the pull-down sits below the gate node.
- Locked MOSFETs are preserved; their associated gate/load/pull-down components are placed around the locked MOSFET instead of silently stacking in the default lane.
- Voltage dividers get distinct columns sized from divider group bounds.
- RC filters get distinct rows with additional room for shunt-capacitor label and symbol stubs.
- Decoupling/bypass capacitors get widened slots near the target. Bulk/reservoir capacitors are placed in a separate rail-bank area so they are not mistaken for small IC decouplers or RC shunts.

## Regression Integrity

Regression expected-code matching is strict. Unexpected `DRC_`, `ERC_`, `VALIDATION_`, and `PARSE_` diagnostics fail a case unless the case explicitly lists them in `expected_codes` or `allowed_codes`. Duplicate diagnostic counts are compared as multisets so repeated warnings cannot disappear inside sorting.

Visual DRC regressions should be fixed in placement/routing/rendering code rather than hidden by the expected-code file.

## Wire/Text Policy

`visual_drc` records all wire/text intersections in metrics for audit visibility. It emits `DRC_WIRE_TEXT_OVERLAP` only for physical routed wires crossing unrelated component reference/value text.

The current conservative policy intentionally does not warn for:

- Power-symbol stubs and net-label stubs.
- A wire segment touching text on a component when the segment endpoint is one of that component's own pins.
- Pin-number proximity, because pin-number text is not yet represented as separate geometry.
- Intentional net-label connection stubs.

This avoids false positives in vertical dividers and local power-symbol attachments while still catching real unrelated wire/text collisions. A fuller text-geometry model is deferred to Milestone 2.

## Determinism

Graph traversals and pattern lists are explicitly sorted by component refs, pattern type, and net names. Reference designators are used only after topology has established equivalent choices.

## Manual Layouts

Existing layout files remain readable. Components already present in an existing layout are preserved, and locked components are not moved or reoriented by topology placement.

## Debugging

Topology analysis is inspectable with:

```powershell
py -3.11 -m circuit_netlist.topology_debug examples/solar_led.cnet
```

The app also exposes:

```text
GET /api/circuit/topology
```

## Source Archive

A clean source ZIP can be created with:

```powershell
py -3.11 tools/create_source_archive.py
```

The archive excludes generated regression output, caches, virtual environments, build folders, Git metadata, coverage artifacts, and ZIP files.

## Current Limitations

- Placement is deterministic and topology-driven, but it is not yet a scored optimizer.
- Pattern placement uses simple local rules rather than a canonical scene-geometry model.
- Flyback detection is present, but the current component library does not include a standalone diode or motor/relay load.
- Ambiguous passives fall back to generic role placement.

Later milestones can build on the `TopologyAnalysis` interface for scored placement, stronger metadata trust, and deeper ERC.
