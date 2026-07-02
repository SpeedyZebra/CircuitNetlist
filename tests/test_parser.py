from pathlib import Path

from circuit_netlist.parser import NetlistParser, parse_engineering_value


ROOT = Path(__file__).resolve().parents[1]


def test_parse_engineering_values() -> None:
    assert parse_engineering_value("100kohm").numeric == 100_000
    assert parse_engineering_value("20mA").numeric == 0.02
    assert parse_engineering_value("100nF").unit == "F"
    assert parse_engineering_value("0.1uF").numeric == 0.1e-6
    assert parse_engineering_value("1\u00b5F").numeric == 1e-6
    assert parse_engineering_value("1\u03bcF").numeric == 1e-6
    assert parse_engineering_value("1000uF").numeric == 1000e-6


def test_parses_example_netlist() -> None:
    circuit, diagnostics = NetlistParser().parse_file(ROOT / "examples" / "solar_led.cnet")
    assert diagnostics == []
    assert circuit is not None
    assert circuit.name == "Solar_LED_Controller"
    assert any(net.name == "LED_ENABLE" for net in circuit.nets)
