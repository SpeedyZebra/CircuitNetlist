# Canonical Scene Geometry

Milestone 2 introduces a typed schematic scene model. The goal is to make rendered SVG, visual DRC, hit testing, export/debug tools, and future placement cleanup read one authoritative geometry tree.

## Core Files

- `src/circuit_netlist/scene.py` defines typed primitives and scene elements.
- `src/circuit_netlist/scene_builder.py` builds a `SchematicScene` from a parsed circuit, component library, layout, and routed nets.
- `src/circuit_netlist/scene_renderer.py` serializes the scene into the existing SVG markup.
- `src/circuit_netlist/hit_testing.py` provides scene-based hit-test helpers.
- `src/circuit_netlist/scene_debug.py` dumps scene JSON and scene-derived SVG from the command line.

## Scene Contract

The canonical scene records:

- Canvas bounds and scene version.
- Component groups, bodies, symbol bounds, pins, pin labels, reference labels, and value labels.
- Physical routed wires.
- Power-symbol, ground-symbol, and net-label attachment stubs.
- Net labels, power labels, ground labels, and junctions.
- Collision bounds and hit bounds where they differ from visible bounds.
- Metadata that preserves current SVG ids/classes and route/component ownership.

The renderer does not recalculate component or wire geometry. `render_circuit` builds the scene and delegates SVG output to `render_scene_svg`.

## Visual DRC

`visual_drc` now builds a scene and delegates to `visual_drc_from_scene`.

The DRC reads:

- `component_body` elements for component overlap checks.
- `component_symbol` collision bounds for wire/symbol checks.
- `pin` elements for legal pin contact exceptions.
- Scene wire and stub elements for wire length and overlap checks.
- Scene text elements for wire/text metrics.

Only physical routed wires crossing unrelated component reference/value text currently emit `DRC_WIRE_TEXT_OVERLAP`; pin and label text are still included in scene geometry for debugging and future policies.

## Debugging

Dump a scene JSON file:

```powershell
py -3.11 -m circuit_netlist.scene_debug examples\solar_led.cnet --json output\scene.json
```

Dump a scene-derived SVG:

```powershell
py -3.11 -m circuit_netlist.scene_debug examples\solar_led.cnet --svg output\scene.svg
```

The running app also exposes:

```text
/api/circuit/scene
```

## Future Work

The scene model is now the shared geometry spine. Later milestones can move more component internals from SVG fragments into explicit render primitives, teach the browser to select from scene ids directly, and feed scene collision geometry back into placement/routing.
