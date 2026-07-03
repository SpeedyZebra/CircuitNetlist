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

## Milestone 2 - canonical schematic scene geometry

Status: complete

Implemented in this branch:

- Milestone 2A foundation: typed scene model, scene JSON, scene debug CLI, and scene-aware DRC entry point.
- Milestone 2B authority: production SVG now serializes canonical scene primitives rather than opaque legacy SVG fragments.
- Milestone 2C presentation cleanup: open schematic symbols use hidden logical bounds without visible body rectangles, while block/package components retain visible bodies.
- Milestone 2D interaction foundation: Playwright browser tests for real component dragging and first-drag center-flash regression coverage.
- Typed scene primitives and elements in `scene.py`.
- `build_schematic_scene` as the shared geometry owner for component bodies, symbol bounds, pin anchors, text bounds, wires, stubs, labels, junctions, and canvas bounds.
- Structured component symbol primitives in `symbol_geometry.py`.
- Neutral text, label, power-symbol, and pin-label helpers in `schematic_geometry.py`.
- Scene-backed SVG rendering through `render_scene_svg` with stable `data-scene-id` attributes.
- Scene-backed visual DRC through `visual_drc_from_scene`.
- Scene hit-testing helpers and `/api/circuit/scene/hit-test`.
- Browser interaction using scene IDs for component, pin, wire, junction, label, and power/ground symbol ownership.
- Cumulative browser component dragging from immutable scene origins, with a 5 pixel click-versus-drag threshold and child-element drag blocking.
- Pointer-down SVG screen CTM snapshots so the first drag and later drags use one coordinate basis.
- Central frontend scene application for load, reload, local upload, reset, and reroute.
- Real PNG bytes from scene primitive rasterization with Pillow.
- `/api/circuit/scene` debug endpoint.
- `scene_debug` CLI for JSON and scene-derived SVG dumps.
- Scene-based export helper functions.
- Regression tests covering scene construction, primitive rendering, DRC delegation, hit testing, app endpoint output, scene export helpers, mutation authority, and import boundaries.
- Symbol-presentation and browser-interaction contract tests.
- Playwright Chromium tests for battery first-drag path sampling, center-flash detection, repeated cumulative drags, reload-first-drag stability, click threshold behavior, and resistor dragging.

Still intentionally limited to Milestone 2 scope:

- Placement and routing consume topology and layout inputs as before; they are not yet driven by scene-level collision solving.
- The legacy renderer registry remains for compatibility but is not used by production scene construction.
- PNG path curves/arcs are approximate in the Pillow raster backend.
- Browser drag still applies a temporary SVG transform until reroute/reload regenerates the scene, and drag persistence remains tied to the explicit Save Layout action.
- Playwright tests require Node.js/npm plus a one-time Chromium install and are not part of the Python-only pytest suite.

## Milestone 3A - constraint-scored placement foundation

Status: complete

Implemented in this branch:

- Python-only placement scoring and bounded local optimization in `constraint_placement.py`.
- Hard constraints for component body overlap, duplicate automatic positions, locked/manual movement, locked obstacle avoidance, and canvas bounds.
- Soft constraints for component clearance, estimated Manhattan wire length, estimated bends, wrong-facing pins, functional flow, group compactness, controller proximity, repeated-group alignment, page area, spread, and aspect ratio.
- Centralized `PlacementWeights` and `PlacementOptimizationConfig`.
- `off`, `score_only`, and `optimize` modes.
- Production default that repairs hard placement violations while preserving soft-only topology heuristic layouts.
- Deterministic candidate generation and budget-limited local search.
- Existing topology-driven placement preserved as the initial candidate.
- Existing/manual layout entries and locked placements treated as fixed obstacles.
- Deterministic `layout.canvas["placement_optimizer"]` metadata without volatile timing.
- `placement_debug` CLI for initial/optimized layouts, score JSON, comparison text, topology JSON, and optimized SVG.
- `/api/circuit/placement-score` for app-side score inspection.
- Audit and design docs in `docs/constraint_placement_audit.md` and `docs/constraint_scored_placement.md`.
- Python tests covering scorer penalties, optimizer modes, locked obstacles, multiple controllers, determinism, budget limits, repeated-group integrity, renamed equivalent circuits, and normal post-routing visual DRC.

Still intentionally limited to Milestone 3A scope:

- This is a scored placement foundation, not a full global placement solver.
- Soft-constraint optimization requires route validation before it can be safely enabled by default.
- The scorer estimates connection cost without invoking full routing.
- Text and power-symbol placement are still validated primarily by scene construction and visual DRC.
- Playwright/browser placement tests remain preserved from Milestone 2D but are deferred until Node.js/npm/Chromium are available locally.

## Milestone 3B - route-validated soft optimization

Status: complete

Implemented in this branch:

- Candidate validation levels for geometry-only, routed, and scene-validated evaluation.
- `RoutedLayoutEvaluation` for final routed quality separate from pre-routing placement score.
- Centralized routed-quality weights in `RoutedLayoutWeights`.
- Baseline route/scene/visual-DRC validation before optimize-mode candidate search.
- Diagnostic count comparison so candidates may remove existing visual diagnostics but may not introduce new ones.
- Two-stage candidate search: cheap placement-score prefilter followed by route/scene validation for a bounded shortlist.
- Route-validated move acceptance using actual routed wire length, actual bend count, maximum net length, scene bounds, aspect ratio, displacement, and orientation-change cost.
- Last-known-valid fallback to the initial scene-validated layout.
- Fast paths for off mode, score-only mode, hard-only clean layouts, and no movable/all-locked layouts.
- Configurable budgets for prefilter evaluations, route validations per pass, total route validations, and wall-clock optimization time.
- Conservative orientation candidates for loose two-pin passives, while strong topology pattern orientations remain protected.
- Strong overlapping topology groups protected from partial movement so shared-controller channels are not pulled apart.
- Driver placement fix so multiple realistic controllers receive distinct channel-aligned positions.
- Solar VBAT overlap fix by aligning power-symbol attachment avoidance with scene DRC symbol padding.
- Extended placement debug CLI output with routed evaluations and per-candidate acceptance/rejection records.
- `/api/circuit/placement-score` metadata now includes routed validation status, route-validation count, rejected-candidate count, budget status, and final diagnostics.
- Full-pipeline tests for route-validated candidate rejection, routed soft improvement, risky no-worse circuits, fast paths, budget exhaustion, orientation behavior, realistic multi-controller circuits, deterministic layout metadata, and solar visual DRC cleanup.

Still intentionally limited to Milestone 3B scope:

- No new routing algorithm or global placement solver.
- Visual diagnostic comparison is code-count based; owner-aware diagnostic deltas are future work.
- Candidate search remains bounded local movement plus conservative passive rotation.
- Electrical diagnostics are preserved by keeping the circuit graph immutable, not by adding a new ERC milestone.
- TODO - execute and expand Playwright browser tests once local Node/npm configuration is working.
