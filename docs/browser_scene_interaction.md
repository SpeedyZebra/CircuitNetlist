# Browser Scene Interaction

The browser UI uses scene IDs and scene metadata from the canonical scene. The same `applySchematic` path is used for initial load, reload, load-case, local upload, reset, and reroute.

## Scene Application

`applySchematic(payload, options)` replaces:

- `state.layout`
- `state.scene`
- `state.sceneById`
- `state.svg`
- diagnostics
- expected-result display
- drag state
- selection state

It rebuilds event bindings after replacing the SVG DOM. Reroute calls this same function so selection and drag logic use the newly returned scene.

## Drag Coordinate Model

SVG component primitives are emitted in immutable scene coordinates. Each component group carries:

- `data-placement-x`
- `data-placement-y`

Those values are the origin used when the current scene was built. During a drag:

```text
svg_delta = pointer_css_delta transformed by the pointer-down SVG screen CTM inverse
proposed_layout_x = layout_start_x + svg_delta_x
proposed_layout_y = layout_start_y + svg_delta_y
visual_translate_x = proposed_layout_x - scene_origin_x
visual_translate_y = proposed_layout_y - scene_origin_y
```

The pointer-down CTM snapshot keeps a drag in one coordinate basis, including the first drag after page load. This keeps first, second, third, and later drags cumulative. Reroute or reload replaces the scene, so the returned placement becomes the new immutable origin.

Invalid non-finite drag coordinates are rejected and do not commit to layout state.

## Click Versus Drag

The drag threshold is `DRAG_THRESHOLD_PX = 5` CSS pixels.

Pointer behavior:

- Pointer down on component artwork records a pending drag only.
- Movement below the threshold remains a click candidate.
- Movement at or above the threshold starts a real drag.
- Pointer up after a drag suppresses the following click event.
- Pointer down on the background pans the canvas.
- Mouse wheel zoom behavior is unchanged.

## Child Element Policy

The following scene kinds do not start component dragging:

- `pin`
- `wire`
- `wire_stub`
- `junction`
- `net_label`
- `net_label_endpoint`
- `power_symbol`
- `ground_symbol`
- `power_label`
- `ground_label`

Pins select exact pin metadata. Wires, junctions, labels, and power/ground symbols select their net. Component symbol artwork, visible bodies, transparent hit areas, and component text resolve to the owning component.

## Save Behavior

Dragging updates the browser layout and marks the current layout dirty. The explicit Save Layout button persists it. Save failures keep the dirty flag set and report the backend error, so the UI does not present an unsaved layout as saved.

## Test Approach

`tests/test_browser_interaction_contract.py` pins critical frontend invariants by inspecting `app.js`: thresholded drag, cumulative transform formula, child-element drag blocking, coherent reroute scene replacement, and save failure behavior.

Milestone 2D adds real Chromium coverage in `tests/browser/component-drag.spec.js`. See `docs/browser_playwright_testing.md` for setup and commands.
