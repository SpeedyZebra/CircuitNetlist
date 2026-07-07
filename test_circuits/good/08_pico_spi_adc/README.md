# Pico SPI ADC

Demonstrates a Raspberry Pi Pico/RP2040 module reading an MCP3008-style SPI ADC.

Main details:
- `R_TOP` and `R_BOTTOM` form a sensor voltage divider.
- `C_FILTER` filters the ADC input.
- `C_ADC` decouples the ADC supply/reference rail.
- Expected diagnostics: clean.
