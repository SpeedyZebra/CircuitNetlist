# Roadmap

## Milestone 1 - topology-driven placement

Status: complete

Implemented in this branch:

- Topology-analysis models and deterministic pattern detection.
- Placement consumption of topology roles and strong-confidence patterns.
- Removal of production solar-example reference placement hacks.
- Topology debug API and CLI.
- Documentation and audit notes.

Still intentionally limited to Milestone 1 scope:

- No general-purpose placement optimizer.
- No canonical scene-geometry/DRC refactor.
- Flyback detection requires future diode/load library entries to become useful.

## Milestone 1.1 - topology cleanup and regression integrity

Status: complete

Implemented in this branch:

- Strict regression expected-code comparison for all diagnostic namespaces.
- Regression fixes for the LED-reversed and MOSFET-floating-gate visual DRC cases.
- Explicit component-role support, local role-intent validation, and role source/reason reporting.
- Ground-first, conservative power-net classification.
- Conservative capacitor role classification before decoupling placement.
- Deterministic lane/slot placement for repeated topology groups.
- Clean source archive helper and ignore rules for generated/cache/build artifacts.

Still intentionally limited to Milestone 1.1 scope:

- No general-purpose scored placement optimizer.
- No canonical scene-geometry rewrite.
- No Milestone 2 architecture changes.

## Milestone 1.2 - robust repeated-group placement and visual-regression coverage

Status: complete

Implemented in this branch:

- Approximate `FunctionalGroupBounds` envelopes local to placement.
- Dimension-aware repeated-group lane spacing for MOSFET channels, voltage dividers, RC filters, and capacitor banks.
- Separate bulk/reservoir capacitor rail-bank placement.
- RC-filter detection guard so power-rail bulk capacitors are not classified as filters.
- Full-pipeline visual DRC tests for repeated groups.
- Six repeated-group stress circuits in the real regression manifest.
- Conservative `DRC_WIRE_TEXT_OVERLAP` policy for physical wire/text collisions.
- Import-safe source archive helper and local verification script for both pytest invocation styles.

Still intentionally limited to Milestone 1.2 scope:

- No canonical scene model.
- No renderer rewrite.
- No router replacement.
- No general-purpose optimizer or constraint solver.

## Milestone 1.3 - component-size-aware functional-group envelopes

Status: complete

Implemented in this branch:

- Functional-group envelopes derived from actual oriented member body bounds.
- Normalized local group positions with conservative routing, pin escape, text, and power/ground margins.
- Lane spacing based on calculated group width and height.
- Debug records for group local positions, oriented bounds, body union, margins, envelope, lane, origin, and fallback geometry.
- Variable-size full-pipeline tests for oversized MOSFET channels, dividers, RC filters, decoupling banks, controllers, and rotated parts.
- Conservative fallback geometry for missing or invalid component dimensions.

Still intentionally limited to Milestone 1.3 scope:

- No canonical scene tree.
- No renderer rewrite.
- No router replacement.
- No global placement optimizer or constraint solver.
