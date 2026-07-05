# Visible Geometry DRC

QA-2 extends visual DRC so it checks visible schematic geometry, not only component bodies and physical routed wires. QA-3 moves the implementation into the shared production module `src/circuit_netlist/visual_drc.py`.

## Checked Geometry

The DRC now includes:

- physical wires
- label stubs and power/ground stubs
- net-label text
- power and ground label text
- component text
- visible component symbols
- net-label endpoint flag shapes
- power and ground symbols

## Diagnostic Codes

New or expanded diagnostics include:

- `DRC_WIRE_LABEL_OVERLAP`
- `DRC_STUB_LABEL_OVERLAP`
- `DRC_LABEL_LABEL_OVERLAP`
- `DRC_POWER_SYMBOL_OVERLAP`
- `DRC_STUB_SYMBOL_OVERLAP`
- `DRC_WIRE_TEXT_OVERLAP`

Metrics with matching overlap counts are included in regression and audit reports.

## Precise Exemptions

Allowed contacts:

- a label stub touching its own label endpoint
- a power/ground stub touching its own symbol
- a wire touching its own component pin
- explicit same-net junction contacts

Rejected contacts:

- a stub passing through another label or symbol
- a wire crossing a net-label endpoint flag
- a power symbol placed on top of unrelated wire or stub geometry
- same-net accidental visual overlap that is not the owning attachment contact

## Placement Cooperation

`LabelPlacementContext` now reserves generated label flags, label text, power symbols, stubs, and pin escape corridors as obstacles for later attachment candidates. Power-symbol scoring treats component body and visible symbol clearance as hard obstacles so generated stubs avoid component artwork.
