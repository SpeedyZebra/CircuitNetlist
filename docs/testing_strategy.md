# Testing Strategy

QA-3 adds pytest markers so development can stay quick without weakening full verification.

## Fast Development

```powershell
pytest -m "not slow and not audit" -q
```

Use this while iterating on parser, library, UI-source, small DRC, and focused unit behavior.

Browser-facing performance work should also check `docs/render_quality_modes.md` and confirm the app still defaults to `interactive` quality.

## Full Python QA

```powershell
pytest -q
python -m pytest -q
```

Use this before commits that touch placement, routing, scene geometry, audit, exports, or shared models.

## Regression

```powershell
python -m circuit_netlist.regression --all
```

Use this to generate the legacy HTML regression report and verify the manifest cases.

## Circuit Audit

```powershell
python -m circuit_netlist.circuit_audit --all
```

Use this for the strict clean/negative quality gate.

## Deferred Browser Tests

Playwright execution remains deferred for QA-3. The existing browser tests remain in the repo but are not part of the required Python-only verification.

Simulation remains deferred.
