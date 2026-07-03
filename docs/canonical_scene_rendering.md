# Canonical Scene Rendering

Milestone 2B makes the canonical scene the production rendering source.

## Pipeline

```text
symbol geometry helpers
        |
        v
structured local/final primitives
        |
        v
build_schematic_scene
        |
        v
render_scene_svg
```

`render_scene_svg` is deliberately narrow. It serializes existing scene primitives and metadata into SVG. It does not calculate component geometry, pin positions, text positions, route points, power-symbol locations, or collision bounds.

## Primitive Types

The renderer serializes:

- `line`
- `rect`
- `circle`
- `polyline`
- `polygon`
- `path`
- `text`

Each primitive belongs to a semantic `SceneElement`. Semantic elements carry ownership and interaction metadata such as component reference, pin name/number, net name, layer, z-order, and selectability.

## Stable Attributes

Visible semantic SVG elements include:

- `data-scene-id`
- `data-kind`
- `data-owner-id`
- `data-component-ref`
- `data-pin-name`
- `data-pin-number`
- `data-net-name`
- `data-selectable`
- `data-layer`

Existing ids and classes are preserved where useful, such as `component-U1`, `pin-U1-1`, `wire-VBAT-0`, `net-VBAT`, and symbol styling classes.

## Component Symbols

`symbol_geometry.py` converts component symbol conventions into structured primitives:

- Resistors use polyline zigzags.
- Capacitors use plate and lead lines.
- LEDs use line, polygon, bar, arrow path, and pin-contact primitives.
- MOSFETs use gate, channel, terminal, arrow, optional diode, and pin-contact primitives.
- DIP/555 symbols use body primitives plus notch, pin-one, and title text primitives.
- Op amps, inductors, ground symbols, test points, and DC sources are also primitive based.

Milestone 2C separates logical bounds from visible body artwork:

- `component_body` is a hidden logical rectangle used for DRC and geometry checks.
- `component_visible_body` is emitted only for symbols that should visually show a package or block.
- Open schematic symbols such as resistors, capacitors, LEDs, MOSFETs, op amps, inductors, test points, and DC sources do not receive an extra visible body rectangle.
- Box and package symbols such as functional blocks, batteries, solar panels, DIP ICs, and the 555 timer retain visible body artwork.

The legacy `RendererRegistry` remains only for compatibility. Production scene construction does not call it.

## DRC And Hit Geometry

Visual DRC consumes scene primitive geometry:

- Hidden `component_body` rectangles provide body collision boxes.
- Wire line primitives provide wire length and overlap data.
- Pin circle primitives provide same-pin contact exemptions.
- Text geometry provides wire/text metrics.

Python hit testing also uses scene primitives, especially line distance for wires and primitive bounds for pins and symbols. Open symbol component selection is carried by the visible `component_group` hit bounds and browser hit-area rectangle, not by rendering the hidden logical body.

## Browser Interaction

Component SVG groups include immutable scene placement metadata:

- `data-placement-x`
- `data-placement-y`

Browser dragging snapshots the SVG screen CTM inverse at pointer-down, converts CSS-pixel pointer deltas into SVG user-unit deltas, then computes `current_layout - scene_origin`. This keeps first and repeated drags cumulative even though the underlying SVG primitives remain in scene coordinates. A drag begins only after 5 CSS pixels of movement. Pins, wires, junctions, net labels, and power/ground symbols keep their own selection behavior and do not initiate component drags.

Reroute responses include layout, scene, SVG, diagnostics, and nested schematic data. The frontend applies them through the same central `applySchematic` path used by load and reload so scene IDs do not go stale.

## PNG Export

PNG export uses:

```text
canonical scene -> canonical SVG generation -> Pillow rasterization of scene primitives
```

This produces valid PNG bytes with scene-derived dimensions. The current Pillow path rasterizes common schematic primitives directly; complex curves/arcs are approximate.
