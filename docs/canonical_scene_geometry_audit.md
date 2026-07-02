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

## Migration Notes

This milestone keeps existing SVG ids, CSS classes, and component renderers intact. Component symbols still use the existing renderer registry for their SVG fragments, while the scene builder records canonical body, symbol, pin, text, and wire geometry beside those fragments.

That keeps the current UI stable and gives later milestones a typed place to replace individual render primitives without rewriting placement or routing at the same time.

## Remaining Limitations

- Component SVG internals are still serialized as SVG fragments, with canonical bounds recorded alongside them.
- PNG export remains a truthful placeholder until a browser canvas conversion or local raster backend is added.
- Placement and routing still predate the scene model; they now feed the scene but are not yet optimized from scene constraints.
- Hit testing is scene-backed in Python helpers, while the browser still mostly uses SVG DOM ids/classes.
