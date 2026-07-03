# Route-Validated Soft Optimization

Milestone 3B makes soft placement optimization route-aware. The topology heuristic still creates the initial schematic layout, but candidates are accepted only after routing, canonical scene construction, and visual DRC comparison.

## Validation Levels

`CandidateValidationLevel` defines the validation ladder:

- `geometry_only`: component bounds, locked constraints, estimated connection cost, flow, groups, and page metrics.
- `routed`: the router has produced candidate routes and route warnings are checked.
- `scene_validated`: the canonical scene has been built and visual DRC has been compared against the baseline.

Optimize mode accepts only scene-validated candidates.

## Baseline Validation

Before candidate search:

1. Score the heuristic layout.
2. Route the heuristic layout.
3. Build the canonical scene.
4. Run visual DRC.
5. Record diagnostic counts and routed metrics.

The baseline diagnostic count is the compatibility reference. A candidate may remove existing visual diagnostics, but it may not add new ones.

## Final Routed Score

`RoutedLayoutEvaluation` separates pre-routing placement score from final routed quality. Final routed quality includes:

- Placement hard-constraint penalty.
- Routing success/failure.
- New unexpected visual DRC count.
- Actual routed wire length.
- Actual bend count.
- Maximum net length.
- Scene content area.
- Scene aspect ratio.
- Displacement from the heuristic layout.
- Orientation-change cost.

Unexpected visual DRC and routing failure invalidate a candidate; their weights remain large for reporting and tie-breaking.

## Candidate Prefilter

Candidate search is two-stage:

1. Generate deterministic move/orientation candidates.
2. Score each candidate with the inexpensive placement scorer.
3. Keep only a small deterministic shortlist.
4. Route and scene-validate shortlisted candidates.

This avoids routing every grid position.

## Acceptance Policy

A candidate is accepted only when:

- Hard placement validity is preserved or improved.
- Locked/manual components remain fixed.
- Routing succeeds.
- The canonical scene builds successfully.
- No new visual DRC diagnostic appears compared with baseline.
- Final routed score improves by at least `min_final_score_improvement`.

Tie-breaking favors fewer hard violations, lower final routed score, shorter actual wire length, fewer bends, smaller displacement, and deterministic candidate ordering.

## Fallback

The optimizer keeps the initial scene-validated layout as the first last-known-valid layout. If every candidate fails, the initial layout is returned. If a later accepted layout is somehow invalid after final resize, the optimizer falls back to the initial layout and records `LAST_KNOWN_VALID_FALLBACK`.

## Fast Paths

Fast paths now report stable `fast_path` values:

- `optimizer_off`
- `score_only`
- `hard_only_clean_layout`
- `no_movable_components`

Budget exits report `budget_reached` and `budget_reason`, such as:

- `MAX_PREFILTER_EVALUATIONS`
- `MAX_ROUTE_VALIDATIONS`
- `MAX_ROUTE_VALIDATIONS_PER_PASS`
- `MAX_OPTIMIZATION_TIME_MS`

## Orientation Policy

Orientation candidates are conservative:

- Only orientation-sensitive two-pin passives are considered.
- Locked/manual components are not rotated.
- Strong topology members are protected.
- Divider resistors, pull resistors, gate resistors, RC filters, decoupling capacitors, and low-side switch group members keep topology-selected orientation.
- Complex symbols such as MCUs, chargers, batteries, MOSFETs, op amps, and timers are not broadly rotated in 3B.

## Debug Output

`placement_debug` writes:

- Initial and optimized layouts.
- Full placement comparison JSON.
- Topology JSON.
- Optimized SVG.
- A human-readable comparison file.

Candidate reports include ID, moved refs, transformation, pre-routing score, estimated connection cost, routing status, actual wire length, bend count, scene bounds, DRC codes, new diagnostics, final score, acceptance/rejection state, rejection reason, and validation time.

The app endpoint `/api/circuit/placement-score` exposes the deterministic layout metadata: initial placement score, final routed score, validation status, optimizer mode, candidate count, route-validation count, rejected-candidate count, budget status, and final diagnostics.

## Determinism

Candidate order is sorted by topology pattern size, pattern type, component refs, score, displacement, and candidate ID. There is no randomness or concurrent routing. Normal layout metadata omits volatile validation timing; timing is available in explicit debug CLI output.

## Known Limits

- This is still a bounded local optimizer, not a global placer.
- The router is unchanged except for using improved attachment collision avoidance.
- Visual DRC comparison is code-count based; richer owner-aware diagnostic comparison remains future work.
- Existing electrical diagnostics are preserved because placement does not mutate the circuit graph.
- Playwright browser execution remains deferred until local Node/npm/Chromium setup is available.
