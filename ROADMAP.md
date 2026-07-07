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
- Flyback pattern inference remains lightweight and schematic-level.

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

## Milestone QA-1 - complete circuit inventory and zero-diagnostic quality gate

Status: complete

Implemented in this branch:

- Complete inventory of physical `.cnet` files and embedded synthetic test netlists in `docs/circuit_inventory.md`.
- Explicit clean and negative manifests in `test_circuits/clean_circuits.yaml` and `test_circuits/negative_circuits.yaml`.
- Strict clean-circuit policy: every clean circuit must parse, validate, analyze topology, place, route, build a canonical scene, pass visual DRC and ERC, export SVG, and export PNG with zero diagnostics.
- Strict negative-fixture policy: every fault circuit must produce exactly its expected diagnostic code multiset and no unrelated DRC/routing/placement/export noise.
- New `circuit_netlist.diagnostics` helper for normalized diagnostic records and shared exact-code comparison.
- New `python -m circuit_netlist.circuit_audit --all` quality-gate CLI.
- Generated audit artifacts under ignored `output/circuit_audit/`: JSON report, Markdown report, SVG exports, and PNG exports.
- Pytest coverage for all clean manifest circuits and all negative manifest circuits.
- `BASIC_DIODE` and a clean relay-coil flyback driver fixture so the motor/relay flyback family is formally covered.
- Regression manifest expanded to 22 legacy cases, including the relay/flyback fixture.
- Fault fixture cleanup to remove unrelated single-pin routing warnings from negative tests.
- Documentation in `docs/circuit_quality_gate.md`.

Still intentionally limited to QA-1 scope:

- No simulation engine, DC solver, SPICE export, solar/weather simulation, or battery simulation.
- No Playwright execution or browser-test expansion in this milestone.
- No new router architecture or placement architecture.
- Embedded unit-test snippets remain pytest-owned synthetic circuits unless promoted into physical `.cnet` fixtures and manifests.
- Deferred: Playwright execution and expansion.
- Deferred: simulation engine.

## Milestone QA-2 - component info, net route-style control, and visible-geometry DRC

Status: complete

Implemented in this branch:

- Library-driven component information metadata for common components, including 555 timer, ATtiny402, charger block, solar panel, battery, resistor, capacitor, LED, MOSFET, op amp, diode, inductor, and DC source.
- Component details API and properties-panel rendering with brief summary, function, status, topology patterns, and connected pin-net table.
- Explicit `NetRouteStyle` model with `auto`, `direct`, `label`, and `power_symbol`.
- Manual route-style overrides saved in layout JSON and exposed through the net properties panel.
- AUTO route-style heuristic with global rail detection, close-net direct routing, label preference for distant/fanout/sense nets, and deterministic direct-candidate obstacle status.
- Route-style debug metadata for selected style, reason, endpoint count, estimated direct length/bends, and direct/label status.
- Stricter visible-geometry DRC for label text, label endpoint flags, stubs, power/ground symbols, power labels, component text, component symbols, and precise ownership exemptions.
- Exact label endpoint collision bounds that match the drawn flag instead of a broad text-plus-padding rectangle.
- Generated label/symbol/stub reservations in placement context so later attachments avoid existing visible geometry.
- Solar visible-overlap audit and fixes for LED_ENABLE/VBAT symbol conflicts and transient VBAT/CHG1 symbol-clearance conflicts.
- Python tests for route-style overrides, API persistence, component metadata/detail payloads, frontend source contracts, visible-geometry DRC diagnostics, ownership exemptions, and solar zero-overlap regression.
- Documentation in `docs/component_information_panel.md`, `docs/net_route_style_policy.md`, `docs/visible_geometry_drc.md`, and `docs/solar_visible_overlap_audit.md`.

Still intentionally limited to QA-2 scope:

- No simulation engine, DC solver, SPICE export, solar/weather simulation, or battery simulation.
- No Playwright execution or browser-test expansion.
- No major router replacement or placement architecture rewrite.
- Direct-vs-label AUTO validation uses deterministic component-obstacle estimates; full scene candidate comparison for every route style remains future work.

## Milestone QA-3 - shared visual DRC and route-style validation

Status: complete

Implemented in this branch:

- Centralized visual DRC in `src/circuit_netlist/visual_drc.py`.
- Regression, circuit audit, app/API rendering, and route-validated placement now import the shared visual DRC module.
- Removed the optimizer's duplicate local visual DRC implementation.
- AUTO route style now performs bounded scene/visual-DRC candidate validation for prioritized nets.
- Route-style debug metadata now records candidates considered, diagnostics by candidate, lengths, bend counts, label/symbol counts, selected candidate, and validation scope.
- Removed exact solar/example-specific net-name routing policy from production router code.
- Preserved semantic power/ground net handling for global rails.
- Fixed `circuit_audit --output` path handling for relative paths, absolute paths, and output directories outside the repo.
- Clarified physical fixture circuits versus embedded unit-test netlists in inventory and quality-gate docs.
- Added pytest markers for `unit`, `integration`, `audit`, and `slow`, plus a documented fast development command.
- Documentation in `docs/visual_drc_architecture.md`, `docs/route_style_validation.md`, and `docs/testing_strategy.md`.

Still intentionally limited to QA-3 scope:

- Route-style candidate validation is bounded and prioritized, not a full global route-style search.
- The router remains the existing Manhattan router; QA-3 does not introduce a major routing replacement.
- Playwright execution remains deferred.
- Simulation remains deferred.

## Milestone QA-4 - interactive render speed

Status: complete

Implemented in this branch:

- Added `RenderQualityMode` with `interactive`, `strict`, and `audit`.
- Added `ManhattanRouter.for_interactive()`, `for_strict()`, and `for_audit()`.
- Browser load, reroute, scene, component details, net details, and export paths default to interactive mode.
- Interactive mode disables per-net scene-validated AUTO route-style candidate search.
- Interactive mode uses placement optimizer mode `off` for normal app rendering.
- Strict regression keeps scene-validated AUTO route-style checks.
- Circuit audit explicitly uses audit router mode.
- Added one central app render pipeline returning circuit, diagnostics, layout, routes, scene, SVG, metrics, and timing.
- Added a current schematic render cache keyed by source, layout, quality mode, and component-library signature.
- Component details and net details reuse cached render context when valid.
- Autoroute now performs one interactive route/scene/render pipeline instead of rerouting after `build_current`.
- Render timing metadata is exposed for debugging.
- Documentation in `docs/render_quality_modes.md`.

Still intentionally limited to QA-4 scope:

- Interactive mode still builds the final scene and runs visual DRC once; scene construction remains the largest remaining hot-path cost.
- No new router algorithm, placement rewrite, Playwright execution, or simulation engine.

## Milestone ERC-1 - shared open/short electrical rule checking

Status: complete

Implemented in this branch:

- Shared production ERC entry point in `src/circuit_netlist/erc.py` through `run_electrical_rules()`.
- Compatibility `ElectricalRuleChecker.check()` wrapper delegates to the shared engine.
- Regression-only electrical checks moved out of `regression.py`.
- App/API rendering, regression, circuit audit, placement/debug paths, and tests use the shared ERC engine.
- Optional pin intent metadata added to the component model and obvious library components.
- Pin-intent metadata added for DC sources, batteries, solar panels, charger pins, MCU supplies, 555 pins, op amp supplies, LEDs, diodes, polarized capacitors, MOSFETs, and ground symbols.
- Terminal-short checks for DC sources, batteries, solar panels, LEDs, diodes, polarized capacitors, IC supply pins, op amp supplies, and charger/regulator critical pins.
- Polarity checks for ground pins on positive rails, positive supply pins on ground, positive terminals on ground, negative terminals on positive rails, and op amp supply reversal.
- Negative DC supply sources are recognized by negative voltage or `role=negative_supply`.
- Required power/ground pin open checks and required control-pin floating checks.
- MOSFET gate floating, gate pull-down, source-ground, and gate-tied-to-power checks remain shared.
- Voltage-divider topology ERC checks are shared in production ERC.
- Visual DRC now reports `DRC_UNRELATED_WIRE_INTERSECTION` for different-net wire crossings while allowing same-net junctions.
- Strict/audit route-style validation now considers more nets so clean fixtures remain crossing-free under the stricter DRC.
- New ERC open/short negative fixture family under `test_circuits/faults/erc_open_short/`.
- Documentation in `docs/electrical_rule_checking.md`.

Still intentionally limited to ERC-1 scope:

- No simulation engine, DC solver, SPICE export, solar/weather simulation, or battery simulation.
- No Playwright execution.
- No major router replacement or placement rewrite.
- ERC remains conservative and metadata-driven; it is not a full analog correctness checker.

## Milestone EC-1 - rendered electrical connectivity validation

Status: complete

Implemented in this branch:

- Shared rendered-connectivity validator in `src/circuit_netlist/rendered_connectivity.py`.
- Scene-derived connectivity graph covering pins, wires, stubs, junctions, net labels, power symbols, and ground symbols.
- Logical equivalence for matching net labels and matching power/ground symbols only after each local stub physically reaches its anchor.
- Direct-wire physical-island checks so `local_wire` nets must connect through actual rendered geometry.
- Diagnostics for missing rendered pins, unreached pins, rendered opens, rendered shorts, disconnected islands, dangling route fragments, disconnected stubs, label/symbol-without-stub cases, text/net mismatches, wrong-net pin contacts, wrong-net label/symbol contacts, and accidental extra pins on a net.
- App/API rendering now reports a `connectivity` diagnostics bucket and rendered-connectivity metrics.
- Legacy regression and circuit audit now run the shared rendered-connectivity validator against the canonical scene.
- Circuit audit has a required `rendered_connectivity` stage for renderable circuits.
- Router simplification now preserves electrical pin endpoints so branch cleanup cannot trim real pin leads back to an internal tee.
- Tests cover valid direct, label, and power-symbol connectivity; rendered gaps; disconnected islands; wrong-net contacts; orphan fragments; app integration; import boundaries; and clean Solar, 555, and repeated-MOSFET scenes.
- Documentation in `docs/rendered_connectivity_validation.md`.

Still intentionally limited to EC-1 scope:

- No new routing algorithm, placement rewrite, or global schematic solver.
- No SPICE export, DC solver, simulation engine, solar/weather simulation, or battery simulation.
- Rendered connectivity validates the canonical scene after routing; it does not choose placements or routes.
- Playwright execution remains deferred.

## Milestone UI-1 - reliable component selection and details panel

Status: complete

Implemented in this branch:

- Component SVG groups now include selectable scene metadata, component reference metadata, and component ID metadata.
- Transparent component hit areas now carry component selection metadata so they are not dead click targets.
- SVG selection now uses delegated click handling from the root schematic SVG.
- Selection priority is pin, then net/wire/label/symbol, then component group/body/symbol/hit-area/text.
- Component pointer-up without movement selects the stored component reference directly, covering the pointer-capture click case.
- Drag release after real movement suppresses the follow-up click so dragging does not accidentally refetch details.
- Basic passive symbols remain selectable through their component group and hit area.
- The properties panel now displays explicit component fields: reference, component ID, human name, summary, function, common use, value, role, package, orientation, metadata status, topology role/patterns, and a pin table.
- Source-level tests cover SVG metadata, hit-area metadata, delegated click handling, pointer-up selection, pin/net path preservation, and representative IC/passive details payloads.
- Documentation in `docs/component_selection_and_details.md`.

Still intentionally limited to UI-1 scope:

- No Playwright execution or browser-test expansion in this milestone.
- No simulation engine.
- No routing, placement, or component-library architecture changes.

## Milestone EX-1 - expanded clean circuit examples with additional MCUs and ICs

Status: complete

Implemented in this branch:

- Eight new zero-diagnostic clean circuits covering Arduino Nano, ESP32 DevKit, Raspberry Pi Pico, LM358, LM393, MCP3008, ULN2003, L293D, level shifting, and WS2812-style LED strips.
- Module-level component definitions for Arduino Nano / ATmega328P, ESP32 DevKit, Raspberry Pi Pico / RP2040, BME280-style I2C sensors, DS18B20-style one-wire sensors, and WS2812-style LED strips.
- Targeted IC/block definitions for LM358 dual op amp, LM393 comparator, MCP3008 SPI ADC, MCP23017 I2C GPIO expander, generic LDO regulator, ULN2003 driver array, L293D motor driver, and unidirectional level shifter.
- Component metadata for the new families, including summaries, common-use text, verified/module status, pin descriptions, electrical types, pin intents, and required supply pins where useful.
- Clean-manifest, legacy regression-manifest, expected-result, and UI load-circuit entries for every new example.
- Audit loading of adjacent saved layout JSON so curated clean examples are validated through the same app-style layout inputs.
- Regression tests for new manifests, component metadata, MCU supply pins, I2C pullups, comparator pullup/hysteresis, MOSFET pulldown/flyback, motor-driver supplies, WS2812 support passives, saved layouts, and friendly UI names.
- Documentation in `docs/expanded_example_circuits.md`, plus inventory and quality-gate updates.

Still intentionally limited to EX-1 scope:

- No simulation engine, DC solver, SPICE export, firmware behavior, or timing analysis.
- No Playwright execution or browser-test expansion.
- No major router replacement, major placement rewrite, hierarchy work, or full component-library trust overhaul.
