# Browser Playwright Testing

Milestone 2D adds a real-browser Playwright suite for schematic interaction. These tests launch the actual FastAPI application, open the real CircuitNetlist viewer, and use Chromium pointer events against scene-derived SVG elements.

## One-Time Setup

Install Node.js first if `node --version` and `npm --version` are not available.

```powershell
npm install
npx playwright install chromium
```

The Python application dependencies still come from the normal Python setup:

```powershell
pip install -r requirements.txt
```

## Commands

```powershell
npm run test:browser
npm run test:browser:headed
npm run test:browser:debug
```

The test runner starts the app automatically through Playwright's `webServer` configuration:

```text
python run.py --host 127.0.0.1 --port 8765 --no-browser
```

The port defaults to `8765`. Override it with:

```powershell
$env:CIRCUIT_NETLIST_TEST_PORT = "8766"
```

If your Python executable is not named `python`, set:

```powershell
$env:CIRCUIT_NETLIST_PYTHON = "py -3.11"
```

## Component Selection

Tests use stable scene-derived selectors, not visible text or SVG ordering:

- `#component-BAT1[data-kind='component_group']`
- `#component-R_LED[data-kind='component_group']`
- `[data-scene-id]`
- `[data-component-ref]`
- `[data-kind]`

The default circuit is the real Solar LED Controller example, which includes `BAT1` and `R_LED`.

## Drag Coordinate Model

The browser drag code uses one coordinate basis per gesture:

1. Pointer down snapshots the SVG `getScreenCTM().inverse()` matrix.
2. Pointer movement is measured in CSS pixels.
3. CSS-pixel deltas are converted to SVG user-unit deltas with the pointer-down matrix.
4. Proposed layout position is `layout_start + svg_delta`.
5. Visual transform is `proposed_layout - scene_origin`.

This avoids mixing SVG points calculated from different CTM snapshots during the first drag after page load.

The drag threshold is `DRAG_THRESHOLD_PX = 5`. Movement below that threshold remains a click.

## Center-Flash Detection

The battery test samples the component bounding box during the first drag. It fails if:

- the component leaves the expected pointer path by more than the configured tolerance,
- consecutive samples jump farther than the pointer path allows,
- the component approaches the SVG or viewport center while the expected path does not,
- the component becomes invisible,
- the SVG transform contains `NaN`, `undefined`, `null`, or non-finite values.

Diagnostics are attached as JSON for each drag path.

## Failure Artifacts

Playwright is configured to keep useful artifacts on failure:

- screenshots,
- traces,
- videos,
- HTML report,
- JSON drag diagnostics attached by the tests.

Ignored output directories:

- `test-results/`
- `playwright-report/`
- `node_modules/`

## Adding Future Browser Tests

Prefer tests that:

- load the real app,
- use scene-derived selectors,
- use real mouse or keyboard events,
- assert visible behavior and state through stable DOM attributes,
- attach diagnostic state on failure.

Good next candidates are pin selection, wire/net selection, reroute followed by net selection, zoom/pan interaction, and save-failure recovery.
