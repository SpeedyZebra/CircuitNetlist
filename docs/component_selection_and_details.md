# Component Selection And Details

UI-1 makes component selection reliable for the canonical SVG schematic.

## Selection Priority

SVG click handling is delegated from the root schematic SVG. The handler resolves the clicked target in this order:

1. Pin marker
2. Net geometry, including wires, labels, junctions, power symbols, and ground symbols
3. Component group, visible body, symbol, transparent hit area, reference text, or value text

This keeps pin and net selection working while making basic passives and block-style parts easy to select.

## Component Targets

Rendered component groups carry semantic selection metadata:

- `data-scene-id`
- `data-kind="component_group"`
- `data-selectable="true"`
- `data-ref`
- `data-component-ref`
- `data-component-id`

The transparent `.hit-area` rectangle inside each component group carries the same component metadata. This prevents the hit area from becoming a dead click target when it catches pointer events outside the visible artwork.

## Click Versus Drag

Component pointer-down still captures the pointer for stable dragging.

Movement below `DRAG_THRESHOLD_PX` remains a click. On `pointerup`, if a component drag never became active, the frontend selects the stored component reference directly with `selectComponentByRef(ref)`. This avoids relying on the browser's later click event, which can be affected by pointer capture.

After actual movement, the frontend suppresses the following click event briefly so drag release does not also refetch component details.

## Properties Panel

Selecting a component fetches:

```text
GET /api/circuit/component-details/{ref}
```

The panel shows:

- reference
- component ID
- human name
- summary
- function and common use
- value and role
- package and orientation
- metadata status
- topology role and patterns
- pin table with number, name, electrical type, connected net, and description

This is especially useful for ICs and functional blocks such as the 555 timer, ATtiny402, charger block, op amp, solar panel, and battery.

## Manual Verification

Playwright execution is deferred for UI-1, so use this manual pass when checking the browser:

1. Start the app with `py -3.11 run.py`.
2. Click a 555 timer body and verify the summary and pin table appear.
3. Click an ATtiny402 body and verify the summary and pin table appear.
4. Click a resistor, capacitor, MOSFET, charger block, solar panel, and battery.
5. Click inside a large component hit area but outside visible artwork.
6. Click a pin marker and verify pin details still appear.
7. Click a wire or net label and verify net details plus the route-style dropdown still appear.
8. Drag a component past the threshold and verify it moves without triggering an unwanted details refresh on release.

## Deferred

Playwright browser execution remains deferred. Simulation remains deferred.
