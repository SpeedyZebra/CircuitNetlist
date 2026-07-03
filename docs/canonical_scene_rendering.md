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

The legacy `RendererRegistry` remains only for compatibility. Production scene construction does not call it.

## DRC And Hit Geometry

Visual DRC consumes scene primitive geometry:

- Component body rectangles provide body collision boxes.
- Wire line primitives provide wire length and overlap data.
- Pin circle primitives provide same-pin contact exemptions.
- Text geometry provides wire/text metrics.

Python hit testing also uses scene primitives, especially line distance for wires and primitive bounds for selectable bodies, pins, and symbols.

## PNG Export

PNG export uses:

```text
canonical scene -> canonical SVG generation -> Pillow rasterization of scene primitives
```

This produces valid PNG bytes with scene-derived dimensions. The current Pillow path rasterizes common schematic primitives directly; complex curves/arcs are approximate.
