# Symbol Presentation Policy

The canonical scene separates visible schematic artwork from logical bounds.

## Scene Element Roles

- `component_group`: selectable parent for browser interaction and group hit area.
- `component_body`: hidden logical bounds used by DRC and geometry checks.
- `component_visible_body`: visible package or block rectangle when the component should display one.
- `component_symbol`: visible symbol primitives such as lines, polygons, circles, paths, and text.

Hidden logical bounds are still real scene geometry. They are not serialized into visible SVG output, but DRC can read their rect primitive and collision bounds.

## Visible Body Policy

Visible bodies are enabled by `scene_builder.has_visible_body`.

Renderers with visible bodies:

- `functional_block`
- `battery`
- `solar_panel`
- `dip_ic`
- `timer_555`

Renderers that remain visually open:

- `resistor`
- `capacitor`
- `polarized_capacitor`
- `inductor`
- `diode`
- `led`
- `nmos`
- `pmos`
- `npn`
- `pnp`
- `op_amp`
- `ground`
- `test_point`
- `dc_source`
- `switch`

Unknown renderers default to a visible body so a future generic block does not disappear.

## Metadata Override

Component definitions may override the renderer policy with `metadata.visual_body`:

- `block`, `package`, or `visible`: force a visible body.
- `none`, `symbol_only`, or `hidden`: suppress a visible body.

Use the override for library-specific presentation decisions. Do not key visible-body behavior on reference designators such as `U1`, `Q1`, or `CHG1`.

## Styling

Only `component_visible_body` emits `.body` artwork. Open symbols should receive selection and search feedback through `.symbol` strokes and transparent component-group hit areas.

The logical body primitive uses class `logical-body` and should not be exposed in production SVG unless a future debug renderer explicitly asks for it.
