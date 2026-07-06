from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from circuit_netlist import app as app_module
from circuit_netlist import circuit_audit, constraint_placement, regression, rendered_connectivity, visual_drc
from circuit_netlist.component_library import load_component_library
from circuit_netlist.models import Circuit, ComponentInstance, Diagnostic, Layout, Net, PinRef, Placement, Severity
from circuit_netlist.topology import TopologyAnalyzer


ROOT = Path(__file__).resolve().parents[1]


def small_circuit() -> tuple[Circuit, Layout]:
    circuit = Circuit(
        name="SmallDRCArchitecture",
        components=[
            ComponentInstance(ref="R1", component_id="BASIC_RESISTOR"),
            ComponentInstance(ref="R2", component_id="BASIC_RESISTOR"),
        ],
        nets=[Net(name="LOCAL", pins=[PinRef(component_ref="R1", pin_name="2"), PinRef(component_ref="R2", pin_name="1")])],
    )
    layout = Layout(components={"R1": Placement(x=100, y=100), "R2": Placement(x=320, y=100)})
    return circuit, layout


def test_visual_drc_shared_module_is_single_production_entrypoint() -> None:
    assert regression.visual_drc_from_scene is visual_drc.visual_drc_from_scene
    assert regression.visual_drc is visual_drc.visual_drc
    assert circuit_audit.visual_drc_from_scene is visual_drc.visual_drc_from_scene
    assert constraint_placement.visual_drc_from_scene is visual_drc.visual_drc_from_scene
    assert app_module.visual_drc_from_scene is visual_drc.visual_drc_from_scene


def test_rendered_connectivity_shared_module_is_single_production_entrypoint() -> None:
    assert app_module.validate_rendered_connectivity is rendered_connectivity.validate_rendered_connectivity
    assert circuit_audit.validate_rendered_connectivity is rendered_connectivity.validate_rendered_connectivity
    assert regression.validate_rendered_connectivity is rendered_connectivity.validate_rendered_connectivity


def test_constraint_optimizer_has_no_local_visual_drc_copy() -> None:
    source = (ROOT / "src" / "circuit_netlist" / "constraint_placement.py").read_text(encoding="utf-8")
    assert "def _visual_drc_from_scene" not in source
    assert "from .visual_drc import visual_drc_from_scene" in source


@pytest.mark.parametrize(
    "code",
    ["DRC_STUB_LABEL_OVERLAP", "DRC_POWER_SYMBOL_OVERLAP", "DRC_WIRE_TEXT_OVERLAP"],
)
def test_optimizer_rejects_new_visible_geometry_diagnostics_from_shared_module(monkeypatch: pytest.MonkeyPatch, code: str) -> None:
    circuit, layout = small_circuit()
    library = load_component_library(ROOT / "components")
    analysis = TopologyAnalyzer().analyze(circuit, library)

    def fake_visual_drc_from_scene(scene):
        return [Diagnostic(severity=Severity.WARNING, code=code, message=f"{code} from shared test")], {"total_wire_length": 0}

    monkeypatch.setattr(constraint_placement, "visual_drc_from_scene", fake_visual_drc_from_scene)
    evaluation = constraint_placement.ConstraintPlacementOptimizer().evaluate_layout(
        circuit,
        library,
        layout,
        analysis,
        baseline_counts=Counter(),
        initial_layout=layout,
    )

    assert "NEW_VISUAL_DRC" in evaluation.rejection_reasons
    assert code in [diag.code for diag in evaluation.unexpected_visual_diagnostics]


def test_no_drc_namespace_is_globally_suppressed() -> None:
    missing, unexpected, matched = regression.compare_diagnostic_codes([], ["DRC_STUB_LABEL_OVERLAP"])
    assert missing == []
    assert unexpected == ["DRC_STUB_LABEL_OVERLAP"]
    assert matched is False
