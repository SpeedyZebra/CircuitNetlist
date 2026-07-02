# Repeated Group Placement Audit

Milestone 1.1 added repeated-pattern lane placement, but the allocator still used mostly fixed coordinate offsets. This audit captures the pre-1.2 behavior and why center-only tests passed while full visual DRC still found collisions.

## Current Group Identification

Repeated functional groups are identified in `src/circuit_netlist/topology.py`:

- `detect_low_side_mosfet_switches` groups a MOSFET, optional LED current-limit branch, optional gate resistor, optional driver, and optional pull-down.
- `detect_led_current_limits` detects resistor/LED series branches.
- `detect_voltage_dividers` detects two-resistor high/mid/low dividers.
- `detect_decoupling_capacitors` emits high-confidence local decoupling/bypass capacitor patterns only.
- `detect_rc_lowpass` detects resistor-series/capacitor-shunt filters.

Placement consumes those patterns in `src/circuit_netlist/placement.py` through `_place_patterns`.

## Pre-1.2 Lane Assignment

Lane indexes were assigned by simple `enumerate(...)` over sorted strong patterns. The offset constants were implicit inside each function:

- LED current limits: `y = 300 + index * 240`.
- Gate resistors: `y = 400 + index * 240`.
- Low-side MOSFET switches: `y = 400 + index * 280`.
- Voltage dividers: `x = 1320 + index * 180`.
- Decoupling capacitors near a target: `x = target.x + 40 + slot * 140`.
- RC filters: `y = 640 + index * 180`.

These offsets separated component centers, not complete group envelopes.

## What Spacing Considered

The 1.1 allocator considered:

- Component orientation for individual body size through `component_size`.
- Locked components through `_set`, which avoids moving locked or existing-layout refs.
- Deterministic ordering through sorted topology patterns.

It did not consider:

- Full body boxes for every member in a functional group.
- Symbol boxes, which can be larger or differently centered than the body.
- Reference/value text boxes.
- Local power-symbol and ground-symbol stubs.
- Net labels and their stubs.
- Router escape corridors.
- Inter-group routing channels.
- Locked component obstruction when selecting a new lane.

## Orientation Effects

Passives rotate between 140x80 and 80x140. The old fixed row spacing did not recalculate lane height after orientation. A vertical pull-down under one channel could occupy the same vertical band as a horizontal gate resistor or LED branch in the next channel.

## Routing Effects

The existing Manhattan router reserves component body obstacles and tries to avoid used edges, but the placement allocator gave it too little free corridor between repeated rows. MOSFET channels need room for:

- Controller-to-gate-resistor routing.
- Gate-resistor-to-gate routing.
- Pull-down connection to the gate/source region.
- LED/resistor branch to MOSFET drain.
- Source-to-ground local symbol attachment.

When rows were only 280 px apart, a lower row could be placed inside the upper row's likely routing and text clearance band.

## Known Failing Stress Circuits

Independent stress testing observed:

- Two-channel MOSFET circuit: 2 component body overlaps, 4 wire/symbol overlaps, 1 unrelated wire overlap.
- Four-channel MOSFET circuit: 6 component body overlaps, 19 wire/symbol overlaps, 7 unrelated wire overlaps.
- Two RC filters plus multiple decoupling capacitors: 1 component body overlap, 8 wire/symbol overlaps, 1 unrelated wire overlap.

Representative collisions included one channel's gate resistor overlapping another channel's LED, and one pull-down overlapping the next gate resistor.

## Why Existing Tests Passed

The repeated-group unit tests only asserted that selected component centers were not identical. Two components can have different centers and still overlap when:

- Their bodies are large or rotated.
- Text and symbol boxes extend beyond body centers.
- Routing stubs pass through a neighboring symbol.
- The row spacing is smaller than the group's complete routed envelope.

Milestone 1.2 replaces center-only coverage with full pipeline tests that run parse, validation, topology analysis, placement, routing, rendering, and visual DRC.

## 1.2 Direction

The 1.2 fix remains local to placement and regression DRC. It does not introduce the future canonical scene model, renderer rewrite, router rewrite, or constraint solver. Group bounds are conservative approximate envelopes used only to pick deterministic lanes with enough clearance for current symbols and the current router.
