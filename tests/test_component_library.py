from pathlib import Path

from circuit_netlist.component_library import ComponentLibrary, load_component_library


ROOT = Path(__file__).resolve().parents[1]


def test_loads_valid_component_definitions() -> None:
    library = load_component_library(ROOT / "components")
    assert not library.diagnostics
    assert library.get("MCU_ATtiny402_SOIC8") is not None


def test_attiny402_has_exactly_eight_pins_and_correct_order() -> None:
    component = load_component_library(ROOT / "components").get("MCU_ATtiny402_SOIC8")
    assert component is not None
    assert len(component.pins) == 8
    assert [(p.number, p.side, p.position) for p in component.pins] == [
        ("1", "left", 1),
        ("2", "left", 2),
        ("3", "left", 3),
        ("4", "left", 4),
        ("5", "right", 4),
        ("6", "right", 3),
        ("7", "right", 2),
        ("8", "right", 1),
    ]


def test_basic_op_amp_has_schematic_pinout() -> None:
    component = load_component_library(ROOT / "components").get("BASIC_OP_AMP")
    assert component is not None
    assert component.body.renderer == "op_amp"
    assert [(p.name, p.side, p.position) for p in component.pins] == [
        ("IN-", "left", 2),
        ("IN+", "left", 4),
        ("OUT", "right", 3),
        ("V+", "top", 4),
        ("V-", "bottom", 4),
    ]


def test_basic_555_timer_has_standard_pinout_with_schematic_sides() -> None:
    component = load_component_library(ROOT / "components").get("BASIC_555_TIMER")
    assert component is not None
    assert component.name == "555 Timer"
    assert component.body.renderer == "timer_555"
    assert [(p.number, p.name, p.side, p.position) for p in component.pins] == [
        ("1", "GND", "bottom", 4),
        ("2", "TRIG", "left", 3),
        ("3", "OUT", "right", 3),
        ("4", "RESET", "top", 4),
        ("5", "CTRL", "bottom", 2),
        ("6", "THRESH", "left", 4),
        ("7", "DISCH", "left", 2),
        ("8", "VCC", "top", 2),
    ]


def test_basic_nmos_uses_generic_nfET_pin_mapping() -> None:
    component = load_component_library(ROOT / "components").get("BASIC_NMOS")
    assert component is not None
    assert component.name == "N-MOSFET"
    assert component.body.renderer == "nmos"
    assert component.metadata["show_body_diode"] is False
    assert [(p.number, p.name, p.side, p.position) for p in component.pins] == [
        ("1", "G", "left", 1),
        ("2", "D", "top", 2),
        ("3", "S", "bottom", 2),
    ]


def test_generic_pmos_uses_source_on_top_symbol_mapping() -> None:
    component = load_component_library(ROOT / "components").get("BASIC_PMOS")
    assert component is not None
    assert component.name == "P-MOSFET"
    assert [(p.number, p.name, p.side, p.position) for p in component.pins] == [
        ("1", "G", "left", 1),
        ("2", "D", "bottom", 2),
        ("3", "S", "top", 2),
    ]


def test_rejects_malformed_yaml(tmp_path: Path) -> None:
    root = tmp_path / "components"
    root.mkdir()
    (root / "bad.yaml").write_text("id: BAD\npins: [", encoding="utf-8")
    library = ComponentLibrary(root).load()
    assert library.diagnostics


def test_rejects_duplicate_component_ids(tmp_path: Path) -> None:
    root = tmp_path / "components"
    root.mkdir()
    body = """
id: DUP
name: Dup
category: Basic
body: {renderer: functional_block}
pins:
  - {number: "1", name: A, side: left, position: 1, electrical_type: passive}
"""
    (root / "a.yaml").write_text(body, encoding="utf-8")
    (root / "b.yaml").write_text(body, encoding="utf-8")
    library = ComponentLibrary(root).load()
    assert any("Duplicate component ID" in d.message for d in library.diagnostics)
