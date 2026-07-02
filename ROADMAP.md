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
