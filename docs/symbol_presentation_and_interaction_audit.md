# Symbol Presentation And Interaction Audit

Milestone 2B made the canonical scene authoritative for rendering, DRC, hit testing, and export. The migration exposed two regressions: logical component body rectangles were rendered as visible schematic artwork for every component, and browser drag state was calculated from a moving origin.

## Symbol Presentation Findings

- `build_schematic_scene` created a `component_body` element for every placed component.
- That body carried a visible `rect` primitive with class `body`.
- `styles.css` colors `.component[data-category="Basic"] .body`, so basic open symbols such as resistors, capacitors, LEDs, MOSFETs, inductors, and op amps showed unintended green rectangles.
- Visual DRC relies on component body rectangles for component-overlap checks.
- Python hit testing and browser selection need component group hit bounds, but they do not need the logical body rectangle to be visible.
- Box/package symbols still need visible bodies: functional blocks, batteries, solar panels, DIP ICs, and the 555 timer package.
- Open schematic symbols should be drawn only from their symbol primitives.

## Interaction Findings

- Dragging updated `state.layout` but visual transforms were calculated from the current drag's starting layout position, while SVG primitives stayed in the original scene coordinate system.
- A later drag could therefore replace a previous transform instead of accumulating it.
- Pointer-down immediately entered component-drag mode, so clicks on selectable child elements could accidentally move a component.
- Pins, wires, junctions, labels, and power/ground symbols need independent selection behavior.
- Reroute updated SVG and layout through a separate path, which risked stale `state.sceneById` metadata.
- Layout save used raw `fetch`, so HTTP failures could be reported as success.

## Implemented Corrections

- `component_body` is now a hidden logical element with `visible=false`, a `logical-body` rect primitive, DRC collision bounds, and hit bounds.
- Visible package artwork is now a separate `component_visible_body` element.
- Open symbols have no `component_visible_body`; their SVG contains symbol primitives and transparent group hit areas only.
- Component groups carry immutable scene placement metadata: `data-placement-x` and `data-placement-y`.
- Component drag uses a pending state and starts only after a 5 CSS pixel threshold.
- Drag transforms use `current_layout - scene_origin`, so repeated drags are cumulative.
- Child scene kinds that represent pins, wires, junctions, labels, or power/ground symbols do not initiate component dragging.
- `applySchematic` centrally replaces layout, scene, scene index, SVG, diagnostics, drag state, and selection state.
- Autoroute responses now include coherent `layout`, `scene`, `svg`, `diagnostics`, and `schematic` payloads.
- Layout save failures now leave the layout dirty and report the backend error.

## Test Coverage Added

- Symbol-presentation tests inspect scene elements and serialized SVG for open and boxed components.
- Hidden logical-body DRC tests prove overlap checks still use non-visible bodies.
- Hit-test tests prove open symbols remain selectable through component group bounds.
- Browser interaction contract tests pin drag threshold, cumulative transform math, child-element drag blocking, reroute scene replacement, and save failure reporting.

## Remaining Limitations

- Real browser interaction tests now live in `tests/browser/component-drag.spec.js`; they require Node.js, npm, and Playwright's Chromium install.
- Dragging updates browser layout state and marks it dirty; persistence still happens through the explicit Save Layout button rather than automatic save on pointer-up.
- PNG path curve rasterization remains approximate, as documented in the canonical scene notes.
