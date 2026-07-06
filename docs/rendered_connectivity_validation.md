# Rendered Connectivity Validation

EC-1 adds rendered electrical connectivity validation in:

```text
src/circuit_netlist/rendered_connectivity.py
```

This stage verifies that the canonical schematic scene electrically matches the parsed netlist. It is intentionally separate from placement and routing so those systems can keep changing while the rendered result is checked by one shared validator.

## What It Checks

Rendered connectivity builds a graph from scene elements:

- component pin anchors
- direct wire segments
- wire stubs
- junctions
- net-label endpoints
- power and ground symbols

The validator compares that graph with the circuit nets and reports stable `Diagnostic` objects when the drawing disagrees with the netlist.

It detects:

- missing rendered pin anchors
- pins not reached by rendered geometry
- rendered opens on expected multi-pin nets
- disconnected direct-wire islands
- dangling or orphaned wire/stub fragments
- labels or power symbols without a connected stub
- label or power-symbol text that does not match its net
- different-net wire contacts and crossings
- wrong-net wires touching pins, labels, or power symbols
- extra pins accidentally contacted by a rendered net

## Net Labels And Power Symbols

Labels and power symbols are treated as logical equivalents only when their local stub physically reaches the label or symbol anchor.

For example, two `VBAT` power symbols can connect the same logical net, but each symbol still needs a physical stub from the component pin to the symbol. A floating `VBAT` symbol does not satisfy connectivity.

Direct-wire nets remain stricter: if a net is rendered as `local_wire`, all expected pins must be connected through physical wire geometry, not just matching labels.

## Consumers

The shared entry point is:

```python
validate_rendered_connectivity(circuit, scene)
```

It is used by:

- app/API rendering
- legacy regression runs
- `python -m circuit_netlist.circuit_audit`
- tests

The app includes rendered-connectivity diagnostics in the main diagnostics list and exposes a `connectivity` bucket plus `metrics["rendered_connectivity"]`. The audit runner has a required `rendered_connectivity` stage for renderable circuits.

## Metrics

The validator returns metrics including:

- graph node and edge counts
- net count
- per-net expected and rendered pins
- route style
- physical island count
- logical island count after label/power-symbol equivalence
- label and power-symbol counts
- dangling segment count
- missing pins

These metrics are diagnostic aids; pass/fail behavior is driven by emitted diagnostics.

## Relationship To ERC And Visual DRC

The validator is not a replacement for ERC or visual DRC.

- ERC checks netlist topology and component electrical intent.
- Visual DRC checks geometry overlaps and visual readability.
- Rendered connectivity checks whether the scene geometry actually connects the intended nets and does not short unrelated nets.

Together they catch different failure modes. A circuit can be ERC-clean while rendered wires are open, and a schematic can be visually clean while a wire endpoint fails to touch a pin.

## Running

Focused tests:

```powershell
python -m pytest tests\test_rendered_connectivity.py -q
```

Quality gate for one circuit:

```powershell
python -m circuit_netlist.circuit_audit --case example_solar_led
```

Full audit:

```powershell
python -m circuit_netlist.circuit_audit --all
```
