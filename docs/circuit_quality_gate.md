# Circuit Quality Gate

QA-1 added a project-wide circuit quality gate for every physical `.cnet` file in the repository. QA-2 extends that gate with stricter visible-geometry checks for label, stub, text, and symbol overlaps. QA-3 centralizes those checks in `src/circuit_netlist/visual_drc.py` so regression, audit, app rendering, and placement candidate validation use the same visual DRC implementation. ERC-1 centralizes topology-level open/short electrical checks in `src/circuit_netlist/erc.py`. EC-1 adds rendered connectivity validation in `src/circuit_netlist/rendered_connectivity.py` so the canonical scene must electrically match the netlist. EX-1 expands the clean fixture set to 21 circuits while keeping the negative fixture set at 24 circuits.

## Clean Versus Negative

Clean circuits are normal examples or good regression fixtures. They must produce zero diagnostics from every checked subsystem:

- `PARSE_`
- `VALIDATION_`
- `ERC_`
- `DRC_`
- `ROUTING_`
- `PLACEMENT_`
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

Physical fixture circuits are first-class `.cnet` files under `examples/`, `test_circuits/`, or related fixture directories. These are fully inventoried and included in clean/negative manifests.

Embedded test netlists are inline snippets used by unit tests to exercise targeted behavior. They are documented separately in `docs/circuit_inventory.md` and are not counted as user-facing fixture circuits unless promoted to physical `.cnet` files.

## Full Pipeline

The audit runner executes:

1. Parse
2. Validate
3. Topology analysis
4. Deterministic placement
5. Manhattan routing
6. Canonical scene build
7. Shared visual DRC from `src/circuit_netlist/visual_drc.py`
8. Rendered connectivity validation from `src/circuit_netlist/rendered_connectivity.py`
9. Shared ERC from `src/circuit_netlist/erc.py`
10. SVG export
11. PNG export when Pillow is available
12. Strict diagnostic comparison

Clean circuits pass only when all diagnostics are empty. Negative circuits pass only when the actual diagnostic code multiset exactly matches the manifest.

When a source file has an adjacent `circuit.layout.json` or example `.layout.json`, the audit runner loads it before placement. The saved layout is treated as an input to the same full pipeline, so routing, canonical scene construction, visual DRC, rendered connectivity, ERC, and export checks still decide whether the circuit is clean.

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
- rendered-connectivity metrics
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

Regression, audit, route-validated placement, and app/API rendering use the shared visual DRC module. App/API rendering, regression, audit, placement/debug paths, and tests use the shared ERC engine. App/API rendering, regression, and audit use the shared rendered-connectivity validator. Regression and audit comparison use the shared code-count comparison helpers so diagnostic namespaces are not silently ignored.

## Adding A Clean Circuit

1. Add the `.cnet` file under `examples/` or `test_circuits/good/`.
2. Add a `test_circuits/clean_circuits.yaml` entry with topology family and purpose.
3. Add the circuit to `test_circuits/manifest.yaml` and `test_circuits/expected/expected_results.yaml` if it belongs in the legacy regression report.
4. Add an adjacent saved layout only when the generated layout is visually cluttered or cannot satisfy the strict scene/DRC gate without manual placement help.
5. Run `python -m circuit_netlist.circuit_audit --case <id>`.
6. Fix the circuit, component metadata, placement, routing, scene geometry, DRC, or ERC until diagnostics are zero.

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
- rendered connectivity warnings/errors for clean circuits
- visible label/stub/symbol/text overlap diagnostics
- rendered opens, shorts, missing pin contacts, dangling stubs, or wrong-net scene contacts
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

Playwright execution remains deferred and is not required for ERC-1 or EC-1.

Simulation remains deferred. ERC-1 and EC-1 do not add a DC solver, SPICE export, solar/weather simulation, or battery simulation.
