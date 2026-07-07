# LM358 Signal Conditioning

Demonstrates a single-supply LM358-style analog front end feeding an MCU ADC.

Main details:
- `R_IN` and `C_IN` form an input RC filter.
- `R_FB` and `R_GAIN` provide a feedback path.
- `R_OUT` isolates the op-amp output from the MCU ADC pin.
- Expected diagnostics: clean.
