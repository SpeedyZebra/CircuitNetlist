# Solar Visible Overlap Audit

QA-2 audited the Solar LED Controller with stricter scene geometry.

## Before Fix

The visible issue around `LED_ENABLE` and `VBAT` was real:

- Net: `LED_ENABLE`
- Element: `net-label-LED_ENABLE-0:stub:1`
- Segment: `(536, 520) -> (536, 336)`
- Overlapped element: `power-symbol-VBAT-2:symbol`
- Symbol bounds: approximately `(512, 368) -> (544, 418)`
- Also overlapped: `power-symbol-VBAT-2:label`
- Label bounds: approximately `(513.2, 368) -> (542.8, 384)`
- Type: label stub through unrelated power symbol and label text

The old DRC missed this because generated power symbols, generated label endpoint shapes, and generated label text were not all treated as DRC obstacles.

During the fix, pin escape corridor scoring exposed a second solar hazard:

- Net: `VBAT`
- Elements: `power-symbol-VBAT-2:stub:0` and `power-symbol-VBAT-2:stub:1`
- Crossed element: `component-CHG1:symbol`
- Type: power-symbol stub through component symbol clearance

The scorer previously treated visible component-symbol clearance as softer than label/text reservations. QA-2 changed that to a hard placement cost matching DRC.

## SUN_SENSE Audit

`SUN_SENSE` is label-routed because it is a named sense/control net crossing functional groups. After QA-2, its label flags and stubs reserve exact visible geometry. The final scene has no `SUN_SENSE` wire/label/stub/symbol overlap diagnostics.

## After Fix

Final solar audit:

- `python -m circuit_netlist.circuit_audit --case example_solar_led`
- Clean circuits: `1/1 passed`
- Unexpected diagnostics: `0`

Final full clean audit:

- `python -m circuit_netlist.circuit_audit --clean-only`
- Clean circuits: `13/13 passed`
- Unexpected diagnostics: `0`

The solar circuit is clean because the visible geometry is clean, not because diagnostics were suppressed.
