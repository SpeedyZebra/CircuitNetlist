# Constraint Placement Audit

Milestones 1 and 2 made schematics more readable by giving placement topology knowledge and by centralizing rendered geometry in the canonical scene. The remaining risk is that placement can still become a pile of one-off rules if every new circuit family adds another special case. Milestone 3A introduces a Python-only scored placement foundation so the current heuristic can remain the seed, while constraint checks and local repairs become reusable.

## Existing Strengths

- `TopologyAnalyzer` classifies component roles, rails, grounds, capacitors, and repeated functional patterns without relying on reference designator names.
- `DeterministicPlacementEngine` already produces useful first-pass layouts for solar LED, op amp, 555 timer, repeated MOSFET channels, dividers, RC filters, and decoupling groups.
- Functional group envelopes use actual oriented component sizes instead of fixed visual guesses.
- `SchematicScene` is now the shared source for rendered component bodies, symbols, pins, labels, wires, stubs, and DRC geometry.
- Manual layouts are respected by treating existing layout entries and `locked=True` placements as fixed.
- Regression DRC catches component body overlap, wire/symbol overlap, unrelated wire overlap, and important physical wire/text overlap.

## Fragile Areas Before Milestone 3A

- Placement quality was implicit. A layout either looked acceptable or failed visual DRC, but there was no comparable score for "better than before."
- Collision checks were spread between renderer/scene DRC and ad hoc placement expectations.
- The heuristic could create good initial layouts, but it had no bounded repair step for accidental hard overlaps.
- Repeated groups were lane-based, but there was no general scoring model for multiple controllers, locked obstacles, page aspect ratio, or connection cost.
- Existing manual layouts were preserved by convention, not by a central hard constraint scorer.
- Debugging placement required inspecting the final SVG or visual DRC report rather than a before/after score breakdown.

## Hard Constraints Added

The scorer in `src/circuit_netlist/constraint_placement.py` now treats these as hard violations:

- Component body boxes must not overlap.
- Automatic components should not share identical coordinates.
- Locked or manually supplied components must not move during optimization.
- Automatic components must avoid locked/manual obstacles.
- Components must stay inside the allowed canvas after placement resize.

These penalties dominate soft preferences so repair never trades a hard collision for a prettier-looking soft metric.

## Soft Constraints Added

Soft penalties score readability and layout quality:

- Minimum clearance between component bodies.
- Estimated Manhattan wire length using a deterministic MST per net.
- Estimated bends and wrong-facing pin connections.
- Left-to-right functional flow based on topology roles.
- Strong functional-group compactness.
- Controller proximity to driven groups.
- Repeated group alignment.
- Placement area, vertical/horizontal spread, and preferred aspect ratio.

The production default keeps `optimize_soft_constraints=False`, so Milestone 3A repairs hard violations without rearranging already-good heuristic layouts. Soft scores are still reported and testable.

## Replaceability

The optimizer is deliberately separate from the topology heuristic:

- `DeterministicPlacementEngine` still builds the initial candidate.
- `ConstraintPlacementOptimizer` receives a complete `Layout` and may return an improved copy.
- `PlacementScorer` can score any layout without moving it.
- Modes are `off`, `score_only`, and `optimize`.
- Weights live in `PlacementWeights`.
- Search limits live in `PlacementOptimizationConfig`.

This keeps future placement strategies replaceable. A later solver can reuse the same score contract or bypass the local optimizer without deleting the heuristic.

## Deferred Browser Work

Milestone 3A is Python-only. The Playwright tests from Milestone 2D remain in the repository and documented in `docs/browser_playwright_testing.md`, but Node/npm/Chromium are not required for this milestone. Expanding Playwright coverage for scored placement is a future task once the local browser toolchain is available.

## Remaining Risks

- The connection-cost model is an estimate. It is intentionally cheaper than full routing and does not replace final router/scene DRC.
- Text clearance is scored indirectly through component clearance and scene DRC, not by running full label placement during scoring.
- The local optimizer moves groups and components by bounded grid offsets; it is not a global constraint solver.
- Functional-group metadata remains descriptive. If a future optimizer aggressively moves group members, group envelope debug records will need to be regenerated after optimization.
- Power-symbol and net-label placement still happens during rendering/scene construction, not during placement scoring.
