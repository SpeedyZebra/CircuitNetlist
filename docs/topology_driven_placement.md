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
- `controller`: MCUs, op amps, and 555 timers.
- `switch`: NMOS and PMOS symbols.
- `load`: LEDs and lighting components.
- `passive`: resistors, capacitors, polarized capacitors, and inductors.
- `unknown`: components without enough information.

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

## Current Limitations

- Placement is deterministic and topology-driven, but it is not yet a scored optimizer.
- Pattern placement uses simple local rules rather than a canonical scene-geometry model.
- Flyback detection is present, but the current component library does not include a standalone diode or motor/relay load.
- Ambiguous passives fall back to generic role placement.

Later milestones can build on the `TopologyAnalysis` interface for scored placement, stronger metadata trust, and deeper ERC.
