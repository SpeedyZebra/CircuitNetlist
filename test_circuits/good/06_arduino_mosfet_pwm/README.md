# Arduino MOSFET PWM Driver

Demonstrates an Arduino Nano PWM pin driving a low-side N-MOSFET through a gate resistor.

Main details:
- `R_PULL` holds the MOSFET gate low at reset.
- `D1` clamps the inductive load.
- `C_DEC` and `C_BULK` support the 5 V rail.
- Expected diagnostics: clean.
