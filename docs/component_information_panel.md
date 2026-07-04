# Component Information Panel

QA-2 adds a library-driven component details payload for the schematic properties panel.

## Metadata Source

Component YAML definitions may provide optional `metadata` fields:

- `summary`
- `function`
- `common_use`
- `pins`
- `notes`
- `warnings`
- `datasheet_url`
- `verified_status`

Pin metadata is keyed by canonical pin name or pin number. The API does not require internet access and does not fetch datasheets.

## API Payload

`GET /api/circuit/component-details/{ref}` returns:

- component reference, component ID, value, category, package, role, and orientation
- metadata summary, function, common use, notes, warnings, datasheet URL, and verified status
- compact pin table with pin number, pin name, electrical type, connected net, description, and expected connection
- detected topology role and patterns when topology analysis finds them

Unknown component definitions fall back to a generic payload with the component ID and an empty pin table.

## Frontend Behavior

Clicking a component calls the component-details endpoint and renders a concise brief in the existing properties panel. The panel is intentionally short: it shows practical schematic context, not a datasheet dump.

Components with QA-2 metadata include the 555 timer, ATtiny402, BQ25185-style charger block, solar panel, LiPo/Li-ion battery, resistor, capacitor, LED, NMOS/PMOS, op amp, diode, inductor, and DC voltage source.
