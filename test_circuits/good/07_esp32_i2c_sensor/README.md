# ESP32 I2C Sensor

Demonstrates a 3.3 V ESP32 DevKit connected to an I2C sensor module.

Main details:
- `UREG1` derives 3.3 V from 5 V.
- `R_SDA` and `R_SCL` are external I2C pullups.
- `R_EN` keeps the ESP32 enable pin high.
- Expected diagnostics: clean.
