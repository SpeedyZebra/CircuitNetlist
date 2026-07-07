# LM393 Comparator With Hysteresis

Demonstrates an LM393-style open-collector comparator feeding an MCU input.

Main details:
- `R_OUT_PULL` pulls the open-collector output high.
- `R_HYST` feeds back output state to the reference node.
- Two resistor dividers provide reference and sensor voltages.
- Expected diagnostics: clean.
