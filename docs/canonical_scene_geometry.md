# Canonical Scene Geometry

Milestone 2 is split into two parts:

- Milestone 2A added the typed scene foundation.
- Milestone 2B made the scene authoritative for production SVG, visual DRC, hit testing, browser identity, and PNG export.

The canonical scene is now the single geometry owner for visible schematic output. The renderer serializes scene primitives; it does not call component SVG renderers or append opaque SVG stored in metadata.

## Core Files

- `src/circuit_netlist/scene.py` defines typed primitives and scene elements.
- `src/circuit_netlist/scene_builder.py` builds a `SchematicScene` from a parsed circuit, component library, layout, and routed nets.
- `src/circuit_netlist/schematic_geometry.py` owns neutral text, label, power-symbol, and attachment geometry helpers.
- `src/circuit_netlist/symbol_geometry.py` returns structured component symbol primitives.
- `src/circuit_netlist/scene_renderer.py` serializes structured scene primitives into SVG.
- `src/circuit_netlist/hit_testing.py` provides scene-based hit-test helpers.
- `src/circuit_netlist/exporters.py` exports SVG and rasterizes scene primitives to PNG with Pillow.
- `src/circuit_netlist/scene_debug.py` dumps scene JSON and scene-derived SVG from the command line.

## Scene Contract

The canonical scene records:

- Canvas bounds and scene version.
- Component groups, hidden logical bodies, optional visible bodies, symbol bounds, pins, pin labels, reference labels, and value labels.
- Physical routed wires.
- Power-symbol, ground-symbol, and net-label attachment stubs.
- Net labels, power labels, ground labels, and junctions.
- Collision bounds and hit bounds where they differ from visible bounds.
- Metadata that preserves current SVG ids/classes and route/component ownership.

Renderable primitives are stored as structured `RenderPrimitive` records such as line, rectangle, circle, polygon, polyline, path, and text. Production scene elements no longer require `metadata["svg"]`.

`render_circuit` builds the scene and delegates SVG output to `render_scene_svg`. The serializer only handles XML escaping, grouping, layer order, attributes, and primitive tag creation.

## Visual DRC

`visual_drc` now builds a scene and delegates to `visual_drc_from_scene`.

The DRC reads:

- Hidden `component_body` primitive rectangles for component overlap checks.
- `component_symbol` collision bounds for wire/symbol checks.
- `pin` elements for legal pin contact exceptions.
- Scene wire and stub line primitives for wire length and overlap checks.
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
/api/circuit/scene/hit-test
```

Browser SVG output includes stable `data-scene-id` attributes. The UI uses those IDs and scene metadata to resolve component, pin, wire, junction, net-label, power-symbol, and ground-symbol selections.

Component groups also include immutable `data-placement-x` and `data-placement-y` scene origins. The browser uses those origins for cumulative drag transforms after Milestone 2C.

## PNG Export

PNG export follows:

```text
canonical scene -> scene SVG generation -> Pillow raster backend over scene primitives -> PNG bytes
```

The SVG is generated first for provenance, and the PNG rasterizer walks the same scene primitive list rather than using legacy component renderers.

## Future Work

The scene model is now the shared geometry spine. Later milestones can improve placement/routing with scene-level constraints, add richer text measurement, and replace approximate path rasterization with a dedicated SVG raster backend if the project needs pixel-perfect PNGs.
