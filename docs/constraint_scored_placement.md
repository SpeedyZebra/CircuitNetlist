# Constraint-Scored Placement

Milestone 3A adds a scored placement foundation without replacing the current topology-driven heuristic. The existing heuristic remains the initial candidate. A bounded optimizer can then score that candidate, optionally repair hard violations, and report a deterministic summary.

## Core Files

- `src/circuit_netlist/constraint_placement.py` defines the scorer, routed-quality weights, optimizer config, comparison models, and bounded local optimizer.
- `src/circuit_netlist/placement.py` still owns the topology heuristic and now calls the optimizer after the initial layout is built.
- `src/circuit_netlist/placement_debug.py` writes explicit before/after debug artifacts.
- `tests/test_constraint_placement.py` covers hard/soft scoring, optimizer modes, locked obstacles, determinism, budget limits, repeated groups, and normal visual DRC.

## Modes

`PlacementOptimizationConfig.mode` controls behavior:

- `off`: score plumbing is bypassed and the heuristic placement is returned.
- `score_only`: the layout is scored and metadata is emitted, but no components move.
- `optimize`: bounded local search can move non-locked automatic components.

The default mode is `optimize` with route-validated soft optimization enabled. Shortlisted candidates are routed, turned into canonical scenes, and checked with visual DRC before acceptance. A safe no-op is preferred over accepting a visually worse schematic.

## Hard Constraints

Hard constraints use very large penalties:

- `NO_COMPONENT_BODY_OVERLAP`
- `UNIQUE_AUTOMATIC_COMPONENT_POSITION`
- `NO_LOCKED_COMPONENT_MOVEMENT`
- `AVOID_LOCKED_OBSTACLES`
- `NO_COMPONENT_OUTSIDE_ALLOWED_CANVAS`

Hard penalties are compared before soft penalties. A candidate with fewer/lower hard violations wins even if it is not visually perfect.

## Soft Constraints

Soft constraints score schematic readability:

- Component body clearance.
- Estimated net connection length.
- Estimated bend count.
- Wrong-facing visual pin directions after rotation.
- Functional flow reversal.
- Strong topology group separation.
- Controller distance to driven group members.
- Repeated group alignment.
- Placement area, spread, and page aspect ratio.

Soft scoring is always available for diagnostics. Soft optimization is route-validated in normal optimize mode and can be disabled with `optimize_soft_constraints=False` for hard-repair-only operation.

## Connection Cost

The scorer estimates connection cost without invoking the full router:

1. Resolve each net endpoint to an absolute visual pin coordinate.
2. Use the rotated visual pin side for facing checks.
3. Build a deterministic Manhattan MST between endpoints.
4. Penalize total Manhattan length.
5. Penalize one bend for endpoint pairs that are not row- or column-aligned.
6. Penalize pin pairs that face away from each other.

This is fast and deterministic. Final wire legality remains the router and scene DRC's responsibility.

## Candidate Generation

The optimizer creates move units in deterministic order:

1. Strong topology patterns above `MIN_PLACEMENT_PATTERN_CONFIDENCE`.
2. Remaining movable components by reference name.

Candidate offsets are generated from configured grid radii. Each candidate is snapped to the configured grid. Locked placements and components supplied by an existing layout are excluded from move units.

The local search is bounded by:

- `max_passes`
- `max_candidates_per_unit`
- `max_total_evaluations`
- `candidate_radii`

If a candidate search ends worse than the initial scene-validated layout, the optimizer falls back to the last known valid layout.

## Route Validation

Milestone 3B adds final routed evaluation:

1. Route the initial heuristic layout.
2. Build the canonical scene.
3. Run visual DRC and record baseline diagnostic counts.
4. Prefilter candidates with the cheap placement score.
5. Route and scene-validate only the shortlist.
6. Reject candidates that add new visual DRC diagnostics or routing warnings.
7. Accept candidates only when final routed score improves.

Final routed score uses actual wire length, actual bend count, maximum net length, scene bounds, aspect ratio, displacement from the heuristic layout, orientation-change cost, and hard validity.

## Manual Layouts

Existing layout entries are treated as manual/fixed. Components with `locked=True` are also fixed. Automatic components are allowed to move around these obstacles, but the locked/manual components themselves may not move.

## Debugging

Run the debug command:

```powershell
python -m circuit_netlist.placement_debug examples\solar_led.cnet --output output\placement_debug
```

Useful options:

```powershell
python -m circuit_netlist.placement_debug examples\solar_led.cnet --mode score_only
python -m circuit_netlist.placement_debug examples\solar_led.cnet --hard-only
python -m circuit_netlist.placement_debug examples\solar_led.cnet --layout examples\555_timer_50_duty_astable.layout.json
```

The command writes:

- `placement_initial.json`
- `placement_optimized.json`
- `placement_score.json`
- `placement_comparison.txt`
- `topology.json`
- `optimized.svg`

The running app exposes:

```text
GET /api/circuit/placement-score
```

Normal `Layout.canvas["placement_optimizer"]` metadata intentionally omits volatile elapsed time so deterministic layout tests stay stable. The debug CLI includes full timing.

## Current Limits

- This is not yet a full global placer.
- Text and symbol placement are not fully simulated inside scoring.
- The optimizer uses local translations and conservative two-pin passive orientation candidates.
- Visual diagnostic comparison is currently code-count based rather than full owner-aware matching.
- Browser/Playwright checks are preserved but deferred for this Python-only milestone.
