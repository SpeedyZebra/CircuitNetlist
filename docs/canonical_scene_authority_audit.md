# Canonical Scene Authority Audit

Milestone 2A created a scene model but left production SVG dependent on opaque legacy SVG strings. Milestone 2B removes that production dependency.

## Previous Data Flow

```text
component renderer registry
        |
        v
opaque SVG strings in scene metadata
        |
        v
scene_renderer appends metadata["svg"]

structured scene geometry
        |
        v
visual DRC / Python hit tests
```

This allowed rendered SVG and DRC geometry to disagree.

## Current Data Flow

```text
circuit + layout + routes
        |
        v
build_schematic_scene
        |
        +--> structured component primitives
        +--> structured wire primitives
        +--> structured text primitives
        +--> structured power/net-label primitives
        |
        +--> render_scene_svg
        +--> visual_drc_from_scene
        +--> hit_test_all
        +--> export_png_from_scene
```

## Opaque SVG Search

Production scene output no longer requires:

- `metadata["svg"]`
- `metadata.get("svg")`
- `registry.render_component(...)`
- `append(element.metadata["svg"])`

The remaining `RendererRegistry.render_component` method is retained only as legacy compatibility code. `scene_builder.py` does not import `renderer.py`.

## Structured Elements

Structured primitives now cover:

- Component bodies.
- Component symbol lines, paths, circles, polygons, and text.
- Pins and pin contacts.
- Pin names and pin numbers.
- Reference/value text.
- Physical wires and stubs.
- Junctions.
- Net-label flags and labels.
- Power and ground symbols.

## Browser Identity

Every visible semantic SVG element carries a stable `data-scene-id`. The browser uses scene IDs and scene metadata to resolve:

- Components.
- Pins.
- Wires.
- Junctions.
- Net labels.
- Power and ground symbols.

CSS classes remain for styling, not as the sole identity system.

## Export

SVG export serializes the scene directly. PNG export generates the canonical scene SVG for provenance and rasterizes the scene primitive list with Pillow.

## Boundary

This milestone does not add a new router, placement optimizer, ERC rules, hierarchy, PCB layout, or netlist syntax.
