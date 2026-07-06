# Visual DRC Architecture

QA-3 centralizes visual schematic DRC in:

```text
src/circuit_netlist/visual_drc.py
```

This module owns scene-based checks for component body overlap, wire/symbol overlap, unrelated wire overlap, wire/text overlap, wire/label overlap, stub/label overlap, label/label overlap, power-symbol overlap, stub/symbol overlap, and precise ownership exemptions for intentional pin, label, and power-symbol contacts.

Rendered electrical connectivity is intentionally separate in `src/circuit_netlist/rendered_connectivity.py`. Visual DRC answers "does this drawing overlap or collide visually?" Rendered connectivity answers "does this drawing electrically connect the same nets as the netlist?"

## Consumers

The following subsystems import the shared module:

- `regression.py`
- `circuit_audit.py`
- `constraint_placement.py`
- `app.py`
- tests

`regression.py` keeps compatibility names for older callers, but those names are imported from `visual_drc.py`. The optimizer no longer has a private `_visual_drc_from_scene()` implementation.

## Policy

Visual DRC is scene-based. Subsystems should build or receive a `SchematicScene`, then call `visual_drc_from_scene(scene)`.

Do not add subsystem-specific DRC suppression. Expected-diagnostic handling belongs in regression/audit comparison code, not in visual DRC.

`rendered_connectivity.py` is not a visual DRC consumer. It consumes the same canonical scene for electrical validation.

Do not add rendered-open or rendered-short checks to `visual_drc.py`. Those belong in the rendered-connectivity validator so geometry readability and electrical continuity remain independently replaceable.
