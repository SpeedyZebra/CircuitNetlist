# WS2812 Level-Shifted LED Strip

Demonstrates a 3.3 V MCU driving a 5 V WS2812-style LED strip through a level shifter.

Main details:
- `UREG1` derives the MCU 3.3 V rail from 5 V.
- `R_DATA` is a series data resistor.
- `C_BULK` supports LED strip surge current.
- Expected diagnostics: clean.
