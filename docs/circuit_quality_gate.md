# Circuit Quality Gate

QA-1 added a project-wide circuit quality gate for every physical `.cnet` file in the repository. QA-2 extends that gate with stricter visible-geometry checks for label, stub, text, and symbol overlaps.

## Clean Versus Negative

Clean circuits are normal examples or good regression fixtures. They must produce zero diagnostics from every checked subsystem:

- `PARSE_`
- `VALIDATION_`
- `ERC_`
- `DRC_`
- `PLACEMENT_`
- `ROUTING_`
- `SCENE_`
- `EXPORT_`
- unclassified diagnostics

Negative circuits are intentional fault fixtures. They must produce exactly the diagnostic codes listed in the negative manifest. Unexpected diagnostics fail the gate. Missing expected diagnostics fail the gate.

## Manifests

Clean circuits live in:

```powershell
test_circuits\clean_circuits.yaml
```

Negative circuits live in:

```powershell
test_circuits\negative_circuits.yaml
```

Each entry records:

- `id`
- `name`
- `path`
- `topology_family`
- `purpose`
- `expected_diagnostics` for negative circuits
- `render_allowed` for negative circuits that can or cannot safely continue through rendering

Clean manifest entries inherit `diagnostics: []`.

## Full Pipeline

The audit runner executes:

1. Parse
2. Validate
3. Topology analysis
4. Deterministic placement
5. Manhattan routing
6. Canonical scene build
7. Visual DRC
8. ERC plus regression ERC checks
9. SVG export
10. PNG export when Pillow is available
11. Strict diagnostic comparison

Clean circuits pass only when all diagnostics are empty. Negative circuits pass only when the actual diagnostic code multiset exactly matches the manifest.

## Audit Command

Run every manifest circuit:

```powershell
python -m circuit_netlist.circuit_audit --all
```

Useful filters:

```powershell
python -m circuit_netlist.circuit_audit --clean-only
python -m circuit_netlist.circuit_audit --negative-only
python -m circuit_netlist.circuit_audit --case solar
```

Outputs are generated under ignored paths:

```text
output/circuit_audit/report.json
output/circuit_audit/report.md
output/circuit_audit/svg/<case>.svg
output/circuit_audit/png/<case>.png
```

## Report Format

`report.json` contains a summary and per-circuit records with:

- stage status
- normalized diagnostics
- expected and actual diagnostic codes
- missing and unexpected diagnostics
- scene and routing metrics
- SVG/PNG output paths
- runtime

`report.md` is a lightweight scoreboard with clean/negative pass counts, total unexpected diagnostics, export counts, and slowest circuits.

## Diagnostic Comparison

Diagnostic normalization lives in `src/circuit_netlist/diagnostics.py`. It records:

- code
- severity
- message
- owner/component/net
- source
- subsystem

Regression, audit, and route-validated placement code use the shared code-count comparison helper so diagnostic namespaces are not silently ignored.

## Adding A Clean Circuit

1. Add the `.cnet` file under `examples/` or `test_circuits/good/`.
2. Add a `test_circuits/clean_circuits.yaml` entry with topology family and purpose.
3. Add the circuit to `test_circuits/manifest.yaml` and `test_circuits/expected/expected_results.yaml` if it belongs in the legacy regression report.
4. Run `python -m circuit_netlist.circuit_audit --case <id>`.
5. Fix the circuit, component metadata, placement, routing, scene geometry, DRC, or ERC until diagnostics are zero.

## Adding A Negative Circuit

1. Add the `.cnet` file under `test_circuits/faults/`.
2. Add a `test_circuits/negative_circuits.yaml` entry.
3. List the exact `expected_diagnostics`.
4. Use `render_allowed: false` only when validation faults make rendering unsafe.
5. Run `python -m circuit_netlist.circuit_audit --case <id>`.
6. Do not add broad allowed diagnostic categories.

## Failure Conditions

The gate fails on:

- parse failure for clean circuits
- validation warnings/errors for clean circuits
- ERC warnings/errors for clean circuits
- visual DRC warnings/errors for clean circuits
- visible label/stub/symbol/text overlap diagnostics
- route warnings
- placement hard violations
- empty scenes
- duplicate scene IDs
- non-finite scene bounds
- missing SVG output
- invalid PNG output when PNG export is available
- unexpected negative-fixture diagnostics
- missing negative-fixture diagnostics

## Deferred Work

Playwright execution remains deferred and is not required for QA-1.

Simulation remains deferred. QA-1 does not add a DC solver, SPICE export, solar/weather simulation, or battery simulation.
