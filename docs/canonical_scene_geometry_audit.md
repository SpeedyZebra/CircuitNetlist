# Canonical Scene Geometry Audit

Before Milestone 2, schematic geometry was split across several owners:

- `renderer.py` generated SVG directly and chose net-label, power-symbol, and ground-symbol attachment geometry while rendering.
- `regression.py` reconstructed many of those same geometry decisions for visual DRC.
- The browser UI selected components from SVG ids/classes rather than a typed scene model.
- Export helpers accepted finished SVG strings, so there was no common model for future PNG/export paths.

This worked for early milestones, but it made cleanup fragile. A change to power-stub placement, pin-label spacing, component symbol bounds, or routed-wire geometry could fix the rendered schematic while leaving visual DRC or hit testing with stale assumptions.

## Duplicated Geometry

The main duplicated calculations were:

- Component body boxes and component symbol boxes.
- Component reference/value text bounds.
- Pin number/name text bounds.
- Physical routed wire segments.
- Net-label stubs and label text bounds.
- Power and ground symbol stubs, symbol bounds, and label text bounds.
- Pin anchor points used to ignore legal wire contact at a connected pin.

Milestone 2 moves those calculations into `build_schematic_scene`. Rendering and visual DRC now consume the same `SchematicScene`.

## Milestone 2A Finding

Milestone 2A kept existing SVG ids and CSS classes, but component symbols still used the renderer registry and stored opaque SVG fragments in scene metadata. That meant DRC could use structured scene bounds while the visible schematic still came from a separate SVG representation.

The old data flow was:

```text
renderer helpers -> opaque SVG fragments -> scene metadata -> SVG output
layout/routes -> scene bounds -> visual DRC
```

This was not authoritative because changing structured scene geometry did not necessarily change the visible SVG.

## Milestone 2B Resolution

The production data flow is now:

```text
circuit + layout + routes
        |
        v
build_schematic_scene
        |
        +--> render_scene_svg
        +--> visual_drc_from_scene
        +--> hit_test_all / hit_test_point
        +--> export_png_from_scene
```

Key changes:

- `scene_builder.py` no longer imports `renderer.py`.
- Component symbols are emitted as structured scene primitives from `symbol_geometry.py`.
- Label, power-symbol, text-box, and pin-label geometry live in neutral `schematic_geometry.py`.
- `scene_renderer.py` serializes primitives directly and does not append `metadata["svg"]`.
- SVG semantic elements include stable `data-scene-id` attributes.
- Browser selection resolves scene IDs before component/net/pin ownership.
- Visual DRC reads body rectangles and wire line primitives from the scene.
- PNG export writes valid PNG bytes from the scene primitive list after generating the canonical scene SVG.

## Remaining Limitations

- Placement and routing still predate the scene model; they now feed the scene but are not yet optimized from scene constraints.
- Browser dragging applies a temporary SVG transform while updating layout state; reroute/reload regenerates the authoritative scene.
- PNG rasterization uses a compact Pillow backend over scene primitives. It is valid PNG output, but complex SVG path curves/arcs are approximate in raster output.
- The old `RendererRegistry` remains in `renderer.py` for compatibility with older tests/imports, but production `render_circuit` uses `build_schematic_scene -> render_scene_svg`.
