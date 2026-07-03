# Circuit Netlist

`circuit_netlist` is a local Python 3.11 schematic viewer for human-readable electronic netlists. It is built around a validated YAML component library, so an AI-generated `.cnet` file must reference known component types and real pins before the app renders a schematic.

The first version focuses on trustable parsing, validation, ERC feedback, component-aware SVG rendering, and an interactive local browser viewer.

## Architecture

- `component_library.py` recursively loads YAML component definitions with Pydantic validation, duplicate checks, aliases, and close-match suggestions.
- `parser.py` uses Lark for the `.cnet` grammar and parses engineering values while preserving original display text.
- `validator.py` resolves component and pin references before rendering.
- `erc.py` runs basic electrical-rule checks with `INFO`, `WARNING`, `ERROR`, and `FATAL` severities.
- `circuit_graph.py` builds a NetworkX graph with separate component, pin, and net nodes.
- `placement.py` defines a replaceable placement engine protocol plus a deterministic first-pass placer.
- `router.py` defines a replaceable routing engine protocol plus a Manhattan router.
- `renderer.py` uses a renderer registry for schematic-style SVG symbols.
- `app.py` serves the FastAPI API and local viewer.

Placement and routing are intentionally modular. The current algorithms are simple and deterministic, but they can be replaced without changing the parser, validator, ERC, or renderer.

## Install On Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pytest
python run.py
```

Open `http://127.0.0.1:8000` if the browser does not open automatically.

## Running

```powershell
python run.py
```

The app loads `examples/solar_led.cnet` automatically and uses `examples/solar_led.layout.json` for saved placement data. Electrical connectivity stays in the `.cnet` file; placement stays in the `.layout.json` file.

## Netlist Syntax

```text
CIRCUIT Solar_LED_Controller

COMPONENT U1 MCU_ATtiny402_SOIC8
COMPONENT R1 BASIC_RESISTOR value=68ohm

NET VBAT:
    U1.VDD
    R1.1
```

Supported features:

- Blank lines and `#` comments
- Component parameters such as `value=100kohm` and `capacity=1000mAh`
- Quoted string values
- Engineering suffixes `p`, `n`, `u`, `m`, `k`, `M`
- Units `ohm`, `V`, `A`, `W`, `F`, `H`, `Ah`, `mAh`
- Pin aliases such as `U1.VCC` resolving to `U1.VDD`

## Component YAML Syntax

```yaml
id: MCU_ATtiny402_SOIC8
name: ATtiny402
category: MCU
manufacturer: Microchip
part_number: ATtiny402
package: SOIC-8
body:
  renderer: dip_ic
  width: 160
  height: 200
  pin_pitch: 40
pins:
  - number: "1"
    name: VDD
    aliases: [VCC]
    side: left
    position: 1
    electrical_type: power_in
```

Valid pin sides are `left`, `right`, `top`, and `bottom`. Valid electrical types are `power_in`, `power_out`, `input`, `output`, `bidirectional`, `passive`, `open_drain`, and `no_connect`.

## Adding A Component

Add a `.yaml` file under `components/<Category>/`. Choose a renderer such as `dip_ic`, `functional_block`, `resistor`, `capacitor`, `polarized_capacitor`, `inductor`, `op_amp`, `led`, `nmos`, `pmos`, `battery`, `solar_panel`, `ground`, or `test_point`.

The loader rejects duplicate component IDs and duplicate physical pin numbers. Use `aliases` for alternate pin names rather than duplicating pins.

MOSFET renderers use a compact no-bulk schematic style by default. NFET symbols use gate left, drain top, source bottom, and an outward source arrow; PFET symbols use gate left, source top, drain bottom, and an inward source arrow. These are basic logical schematic components, not package-specific part pinouts. Generic MOSFETs default to `metadata: {mosfet_mode: enhancement, show_body_diode: false}`.

## Viewer

The browser UI provides:

- Inline SVG schematic canvas
- Mouse-wheel zoom and drag-to-pan
- Grid and snap toggles
- Component dragging
- Load Circuit modal for examples, regression cases, and local `.cnet` uploads
- Drag-and-drop `.cnet` loading on the schematic canvas
- Component, pin, and net inspection
- Net highlighting
- Search for references and nets
- Reload, reroute, save-layout, and SVG export buttons
- Validation and ERC panel

### Loading Circuits

Click **Load Circuit** in the toolbar to open the loader.

- **Examples** lists normal project examples such as `examples/solar_led.cnet`.
- **Regression Cases** lists good and fault cases from `test_circuits/manifest.yaml` and shows expected diagnostic codes where available.
- **Open Local File** lets you choose a `.cnet` file and optionally a matching `.layout.json`. The browser reads the file text and sends the text to the backend; arbitrary local Windows paths are never sent or trusted.

You can also drag a `.cnet` file onto the schematic canvas. If you drop a `.layout.json` at the same time, the app will try to use it as the starting layout.

The toolbar shows the current circuit name and source file. **Reload** rereads built-in cases from disk; uploaded files reload from the last text held in application memory because the backend cannot safely reread a browser-local path.

## API

Implemented endpoints:

- `GET /api/components`
- `GET /api/components/{component_id}`
- `GET /api/circuit`
- `GET /api/circuits`
- `GET /api/circuits/{case_id}`
- `GET /api/circuit/current`
- `POST /api/circuit/load`
- `POST /api/circuit/load-case`
- `POST /api/circuit/load-text`
- `POST /api/circuit/reload`
- `POST /api/circuit/validate`
- `POST /api/layout/save`
- `POST /api/layout/autoroute`
- `GET /api/export/svg`
- `GET /api/health`

## Tests

```powershell
pytest
```

The tests cover component loading, malformed YAML, duplicate IDs, parsing, engineering values, validation, pin aliases, ERC checks, deterministic placement, non-overlap, and stable SVG IDs.

Browser interaction tests use Playwright and launch the real app on a dedicated test port. One-time setup:

```powershell
npm install
npx playwright install chromium
```

Run them with:

```powershell
npm run test:browser
```

Headed and debug variants are available through `npm run test:browser:headed` and `npm run test:browser:debug`. See `docs/browser_playwright_testing.md` for details.

## Regression Suite

The `test_circuits/` tree is a browser-readable schematic validation and regression suite. It currently implements the first vertical slice:

- `01_led_resistor`
- `02_mosfet_switch`
- `03_voltage_divider_adc`

Each good case has a `circuit.cnet`, `circuit.layout.json`, and `README.md`. Fault cases live under `test_circuits/faults/` and are compared against stable diagnostic codes in `test_circuits/expected/expected_results.yaml`.

Run all regression cases from PowerShell:

```powershell
python -m circuit_netlist.regression --all
```

Useful variants:

```powershell
python -m circuit_netlist.regression --good-only
python -m circuit_netlist.regression --faults-only
python -m circuit_netlist.regression --case 03_voltage_divider_adc
python -m circuit_netlist.regression --open-report
```

Outputs are written under:

```text
test_circuits/generated/
├── svg/
├── json/
└── report/index.html
```

The report embeds SVG previews and lists expected codes, actual codes, missing expected findings, unexpected findings, visual DRC metrics, detected patterns, and numeric calculations.

### Adding A Regression Case

1. Add a folder under `test_circuits/good/<family>/` or a fault netlist under `test_circuits/faults/<family>/`.
2. Add the case to `test_circuits/manifest.yaml`.
3. Add expected parse/validation status and expected codes to `test_circuits/expected/expected_results.yaml`.
4. Run `python -m circuit_netlist.regression --case <family>`.

Expected results compare stable codes such as `ERC_LED_NO_CURRENT_LIMIT`, `ERC_MOSFET_GATE_FLOATING`, `ERC_DIVIDER_BOTTOM_MISSING`, `VALIDATION_UNKNOWN_PIN`, and `DRC_WIRE_SYMBOL_OVERLAP`; avoid comparing free-form message strings.

Numeric checks are heuristic, not SPICE simulation. The current slice estimates LED current and voltage-divider output where component metadata exists.

## Known Limitations

- Auto-placement is deterministic and dimension-aware, but still basic.
- Routing is grid-based orthogonal A* routing around padded component bodies, but it is still a coarse first-pass schematic router.
- The regression suite currently covers the first three requested circuit families. Op-amp, 555 timer, motor/flyback, solar-charger numeric compatibility, and cabin-system rules are documented targets but not complete yet.
- Visual DRC records wire/text metrics, but the vertical slice treats hard component/wire-symbol/wire-wire collisions as the primary pass/fail checks.
- Dragging components updates component positions immediately; wire rerouting is recalculated on reload/autoroute rather than continuously during drag.
- PNG export is represented in the UI, but raster export needs a browser canvas conversion or a local SVG raster backend before it should be considered complete.
- `MCU_ATtiny404_SOIC14` is intentionally marked as an incomplete placeholder and should not be used as a production pinout.

## Roadmap

- Add pluggable A* routing with stronger obstacle avoidance.
- Add manual wire waypoint editing.
- Add component placement from the library panel.
- Add a real PNG export backend.
- Add richer ERC rules for regulator/load compatibility and current paths.
- Add more physical package renderers and verified component pinouts.
