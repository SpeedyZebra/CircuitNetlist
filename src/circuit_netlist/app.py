from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .component_library import ComponentLibrary, load_component_library
from .erc import ElectricalRuleChecker
from .exporters import export_svg
from .models import Circuit, Diagnostic, Layout, RenderedCircuit, Severity
from .parser import NetlistParser
from .placement import DeterministicPlacementEngine
from .regression import compare_diagnostic_codes, detect_patterns, engineering_calculations, visual_drc
from .renderer import render_circuit
from .router import ManhattanRouter
from .scene_builder import build_schematic_scene
from .topology import TopologyAnalyzer
from .validator import CircuitValidator, has_blocking_diagnostics


ROOT = Path(__file__).resolve().parents[2]
COMPONENT_ROOT = ROOT / "components"
EXAMPLE_NETLIST = ROOT / "examples" / "solar_led.cnet"
EXAMPLE_LAYOUT = ROOT / "examples" / "solar_led.layout.json"
INVERTING_OP_AMP_NETLIST = ROOT / "examples" / "inverting_op_amp.cnet"
TIMER_555_NETLIST = ROOT / "examples" / "555_timer_50_duty_astable.cnet"
TEST_CIRCUITS_ROOT = ROOT / "test_circuits"
STATIC_ROOT = Path(__file__).resolve().parent / "static"


class LoadRequest(BaseModel):
    path: str = str(EXAMPLE_NETLIST)


class LayoutSaveRequest(BaseModel):
    layout: Layout


class LoadTextRequest(BaseModel):
    filename: str
    text: str
    layout_text: str | None = None


class LoadCaseRequest(BaseModel):
    case_id: str


class CurrentCircuitState(BaseModel):
    current_circuit_id: str = "example_solar_led"
    current_circuit_name: str = "Solar_LED_Controller"
    source_kind: str = "example"
    source_filename: str = "examples/solar_led.cnet"
    source_text: str | None = None
    layout_text: str | None = None
    case_id: str | None = "example_solar_led"
    layout_dirty: bool = False
    validation_findings: list[dict[str, Any]] = []
    erc_findings: list[dict[str, Any]] = []
    drc_findings: list[dict[str, Any]] = []
    expected_codes: list[str] = []
    actual_codes: list[str] = []
    expected_match: bool | None = None


app = FastAPI(title="Circuit Netlist")
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")
CURRENT_STATE = CurrentCircuitState(
    source_text=EXAMPLE_NETLIST.read_text(encoding="utf-8") if EXAMPLE_NETLIST.exists() else None,
    layout_text=EXAMPLE_LAYOUT.read_text(encoding="utf-8") if EXAMPLE_LAYOUT.exists() else None,
)


def library() -> ComponentLibrary:
    return load_component_library(COMPONENT_ROOT)


def read_layout() -> Layout | None:
    if not EXAMPLE_LAYOUT.exists():
        return None
    return Layout.model_validate_json(EXAMPLE_LAYOUT.read_text(encoding="utf-8"))


def write_layout(layout: Layout) -> None:
    EXAMPLE_LAYOUT.write_text(layout.model_dump_json(indent=2), encoding="utf-8")


def build_current(layout_override: Layout | None = None, use_saved_layout: bool = True) -> RenderedCircuit:
    lib = library()
    parser = NetlistParser()
    circuit, diagnostics = parser.parse_file(EXAMPLE_NETLIST)
    diagnostics.extend(lib.diagnostics)
    if circuit is None:
        return RenderedCircuit(svg=render_error_svg(diagnostics), diagnostics=diagnostics, layout=Layout(), circuit=None)
    diagnostics.extend(CircuitValidator(lib).validate(circuit))
    existing_layout = layout_override if layout_override is not None else (read_layout() if use_saved_layout else None)
    layout = DeterministicPlacementEngine().place(circuit, lib, existing_layout)
    routes = []
    if not has_blocking_diagnostics(diagnostics):
        diagnostics.extend(ElectricalRuleChecker(lib).check(circuit))
        routes = ManhattanRouter().route(circuit, lib, layout)
        for route in routes:
            for warning in route.warnings:
                diagnostics.append(Diagnostic(severity=Severity.WARNING, message=warning))
    svg = render_circuit(circuit, lib, layout, routes, diagnostics)
    return RenderedCircuit(svg=svg, diagnostics=diagnostics, layout=layout, circuit=circuit)


def render_loaded_circuit(
    text: str,
    filename: str,
    source_kind: str,
    circuit_id: str,
    case_id: str | None = None,
    layout_text: str | None = None,
    update_state: bool = True,
) -> dict[str, Any]:
    lib = library()
    parser = NetlistParser()
    circuit, diagnostics = parser.parse_text(text, Path(filename))
    validation_findings: list[Diagnostic] = []
    erc_findings: list[Diagnostic] = []
    drc_findings: list[Diagnostic] = []
    layout = Layout()
    routes = []
    svg = render_error_svg(diagnostics)
    render_allowed = circuit is not None
    layout_loaded = False
    expected_codes = expected_codes_for_case(case_id)
    patterns: list[dict[str, Any]] = []
    calculations: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}

    if circuit is not None:
        validation_findings = CircuitValidator(lib).validate(circuit)
        diagnostics.extend(validation_findings)
        render_allowed = not has_blocking_diagnostics(diagnostics)
        if render_allowed:
            erc_findings = ElectricalRuleChecker(lib).check(circuit)
            diagnostics.extend(erc_findings)
            existing_layout = parse_layout_text(layout_text)
            layout_loaded = existing_layout is not None
            layout = DeterministicPlacementEngine().place(circuit, lib, existing_layout)
            routes = ManhattanRouter().route(circuit, lib, layout)
            for route in routes:
                for warning in route.warnings:
                    diagnostics.append(Diagnostic(severity=Severity.WARNING, message=warning))
            drc_findings, metrics = visual_drc(circuit, lib, layout, routes)
            diagnostics.extend(drc_findings)
            patterns = detect_patterns(circuit, lib)
            calculations = engineering_calculations(circuit, lib)
            svg = render_circuit(circuit, lib, layout, routes, diagnostics)

    actual_codes = sorted({diag.code for diag in diagnostics if diag.code})
    expected_match = compare_expected_codes(expected_codes, actual_codes) if case_id else None
    success = circuit is not None and not has_blocking_diagnostics(validation_findings)
    if update_state and success:
        CURRENT_STATE.current_circuit_id = circuit_id
        CURRENT_STATE.current_circuit_name = circuit.name
        CURRENT_STATE.source_kind = source_kind
        CURRENT_STATE.source_filename = filename
        CURRENT_STATE.source_text = text
        CURRENT_STATE.layout_text = layout_text
        CURRENT_STATE.case_id = case_id
        CURRENT_STATE.layout_dirty = False
        CURRENT_STATE.validation_findings = [diag.model_dump(mode="json") for diag in validation_findings]
        CURRENT_STATE.erc_findings = [diag.model_dump(mode="json") for diag in erc_findings]
        CURRENT_STATE.drc_findings = [diag.model_dump(mode="json") for diag in drc_findings]
        CURRENT_STATE.expected_codes = expected_codes
        CURRENT_STATE.actual_codes = actual_codes
        CURRENT_STATE.expected_match = expected_match
    return {
        "success": success,
        "circuit_id": circuit_id,
        "circuit_name": circuit.name if circuit else None,
        "filename": filename,
        "source_kind": source_kind,
        "case_id": case_id,
        "validation": [diag.model_dump(mode="json") for diag in validation_findings],
        "erc": [diag.model_dump(mode="json") for diag in erc_findings],
        "drc": [diag.model_dump(mode="json") for diag in drc_findings],
        "diagnostics": [diag.model_dump(mode="json") for diag in diagnostics],
        "render_allowed": render_allowed,
        "schematic": {"svg": svg, "layout": layout.model_dump(mode="json"), "circuit": circuit.model_dump(mode="json") if circuit else None},
        "layout_loaded": layout_loaded,
        "expected": {"codes": expected_codes, "actual_codes": actual_codes, "match": expected_match},
        "patterns": patterns,
        "calculations": calculations,
        "metrics": metrics,
    }


def parse_layout_text(layout_text: str | None) -> Layout | None:
    if not layout_text:
        return None
    try:
        return Layout.model_validate_json(layout_text)
    except Exception:
        return None


def compare_expected_codes(expected: list[str], actual: list[str]) -> bool:
    return compare_diagnostic_codes(expected, actual)[2]


def circuit_catalog() -> dict[str, Any]:
    examples = [
        catalog_item("example_solar_led", "Solar LED Controller", EXAMPLE_NETLIST, "example", "Solar charger, battery, ATtiny402, MOSFET, LED"),
        catalog_item("example_inverting_op_amp", "Inverting Op Amp", INVERTING_OP_AMP_NETLIST, "example", "Classic inverting amplifier with gain -R2/R1"),
        catalog_item("example_555_timer_50_duty_astable", "555 Timer 50% Duty Astable", TIMER_555_NETLIST, "example", "555 timer astable oscillator with timing capacitor and output feedback"),
    ]
    regression: dict[str, list[dict[str, Any]]] = {"good": [], "faults": []}
    manifest = load_manifest()
    expected = load_expected_results()
    for case in manifest.get("cases", []):
        path = TEST_CIRCUITS_ROOT / case["path"]
        title = human_title(case["family"], case["path"])
        item = catalog_item(case["id"], title, path, f"regression_{case['type']}", "", case=case, expected=expected.get(case["path"], {}))
        regression["good" if case["type"] == "good" else "faults"].append(item)
    return {"examples": examples, "regression": regression}


def catalog_index() -> dict[str, dict[str, Any]]:
    items = {item["id"]: item for item in circuit_catalog()["examples"]}
    for group in circuit_catalog()["regression"].values():
        for item in group:
            items[item["id"]] = item
            items[item["case_path_no_ext"]] = item
    return items


def catalog_item(case_id: str, title: str, path: Path, kind: str, description: str, case: dict[str, Any] | None = None, expected: dict[str, Any] | None = None) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    circuit, _ = NetlistParser().parse_text(text, path)
    return {
        "id": case_id,
        "title": title,
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "case_path_no_ext": str(path.relative_to(TEST_CIRCUITS_ROOT)).replace("\\", "/").removesuffix(".cnet") if TEST_CIRCUITS_ROOT in path.parents else case_id,
        "description": description or title,
        "kind": kind,
        "family": case.get("family") if case else None,
        "type": case.get("type") if case else "example",
        "component_count": len(circuit.components) if circuit else 0,
        "net_count": len(circuit.nets) if circuit else 0,
        "expected_codes": list((expected or {}).get("expected_codes", [])),
    }


def human_title(family: str, path: str) -> str:
    name = Path(path).stem
    if name == "circuit":
        name = family
    return name.replace("_", " ").title()


def load_manifest() -> dict[str, Any]:
    path = TEST_CIRCUITS_ROOT / "manifest.yaml"
    if not path.exists():
        return {"cases": []}
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8")) or {"cases": []}


def load_expected_results() -> dict[str, Any]:
    path = TEST_CIRCUITS_ROOT / "expected" / "expected_results.yaml"
    if not path.exists():
        return {}
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("circuits", {})


def expected_codes_for_case(case_id: str | None) -> list[str]:
    if not case_id:
        return []
    item = catalog_index().get(case_id)
    if not item:
        return []
    rel = item["path"]
    if not rel.startswith("test_circuits/"):
        return []
    key = rel.removeprefix("test_circuits/")
    return list(load_expected_results().get(key, {}).get("expected_codes", []))


def layout_for_path(path: Path) -> str | None:
    if path.resolve() == EXAMPLE_NETLIST.resolve() and EXAMPLE_LAYOUT.exists():
        return EXAMPLE_LAYOUT.read_text(encoding="utf-8")
    example_layout = path.with_suffix(".layout.json")
    if path.is_relative_to(ROOT / "examples") and example_layout.exists():
        return example_layout.read_text(encoding="utf-8")
    layout_path = path.with_name("circuit.layout.json")
    return layout_path.read_text(encoding="utf-8") if layout_path.exists() else None


def render_error_svg(diagnostics: list[Diagnostic]) -> str:
    lines = "".join(f'<text x="24" y="{40 + i * 24}" fill="#ff9c9c">{diag.severity}: {diag.message}</text>' for i, diag in enumerate(diagnostics))
    return f'<svg id="schematic" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 700"><rect width="1200" height="700" fill="#111821"/>{lines}</svg>'


def current_scene_payload() -> dict[str, Any]:
    lib = library()
    parser = NetlistParser()
    source_text = CURRENT_STATE.source_text or (EXAMPLE_NETLIST.read_text(encoding="utf-8") if EXAMPLE_NETLIST.exists() else "")
    source_filename = CURRENT_STATE.source_filename or str(EXAMPLE_NETLIST)
    circuit, diagnostics = parser.parse_text(source_text, Path(source_filename))
    if circuit is None:
        return {"scene": None, "diagnostics": [diag.model_dump(mode="json") for diag in diagnostics]}
    validation = CircuitValidator(lib).validate(circuit)
    diagnostics.extend(validation)
    if has_blocking_diagnostics(diagnostics):
        return {"scene": None, "diagnostics": [diag.model_dump(mode="json") for diag in diagnostics]}
    diagnostics.extend(ElectricalRuleChecker(lib).check(circuit))
    existing_layout = parse_layout_text(CURRENT_STATE.layout_text)
    layout = DeterministicPlacementEngine().place(circuit, lib, existing_layout)
    routes = ManhattanRouter().route(circuit, lib, layout)
    for route in routes:
        for warning in route.warnings:
            diagnostics.append(Diagnostic(severity=Severity.WARNING, message=warning))
    scene = build_schematic_scene(circuit, lib, layout, routes)
    return {
        "scene": scene.model_dump(mode="json"),
        "diagnostics": [diag.model_dump(mode="json") for diag in diagnostics],
        "layout": layout.model_dump(mode="json"),
        "route_count": len(routes),
    }


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_ROOT / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/components")
def get_components() -> dict[str, object]:
    lib = library()
    return {"components": [component.model_dump(mode="json", exclude={"source_path"}) for component in lib.all()], "diagnostics": [d.model_dump() for d in lib.diagnostics]}


@app.get("/api/components/{component_id}")
def get_component(component_id: str) -> dict[str, object]:
    component = library().get(component_id)
    if not component:
        raise HTTPException(status_code=404, detail="Component not found")
    return component.model_dump(mode="json", exclude={"source_path"})


@app.get("/api/circuit")
def get_circuit(fresh: bool = False) -> dict[str, object]:
    if CURRENT_STATE.source_text and not fresh:
        response = render_loaded_circuit(
            CURRENT_STATE.source_text,
            CURRENT_STATE.source_filename,
            CURRENT_STATE.source_kind,
            CURRENT_STATE.current_circuit_id,
            CURRENT_STATE.case_id,
            CURRENT_STATE.layout_text,
            update_state=False,
        )
        schematic = response["schematic"]
        return {**schematic, "diagnostics": response["diagnostics"], "current": CURRENT_STATE.model_dump(mode="json"), "expected": response["expected"]}
    rendered = build_current(use_saved_layout=not fresh)
    return {**rendered.model_dump(mode="json"), "current": CURRENT_STATE.model_dump(mode="json")}


@app.get("/api/circuits")
def get_circuits() -> dict[str, Any]:
    return circuit_catalog()


@app.get("/api/circuits/{case_id:path}")
def get_circuit_metadata(case_id: str) -> dict[str, Any]:
    item = catalog_index().get(case_id)
    if not item:
        raise HTTPException(status_code=404, detail="Circuit case not found")
    path = (ROOT / item["path"]).resolve()
    if not (path.is_relative_to(ROOT / "examples") or path.is_relative_to(TEST_CIRCUITS_ROOT)):
        raise HTTPException(status_code=403, detail="Circuit path is not allowed")
    return {**item, "text": path.read_text(encoding="utf-8")}


@app.post("/api/circuit/load-text")
def load_text(request: LoadTextRequest) -> dict[str, Any]:
    if not request.filename.lower().endswith(".cnet"):
        raise HTTPException(status_code=400, detail="Only .cnet files are supported")
    safe_name = Path(request.filename).name
    return render_loaded_circuit(request.text, safe_name, "uploaded", f"uploaded_{safe_name}", layout_text=request.layout_text)


@app.post("/api/circuit/load-case")
def load_case(request: LoadCaseRequest) -> dict[str, Any]:
    item = catalog_index().get(request.case_id)
    if not item:
        raise HTTPException(status_code=404, detail="Circuit case not found")
    path = (ROOT / item["path"]).resolve()
    if not (path.is_relative_to(ROOT / "examples") or path.is_relative_to(TEST_CIRCUITS_ROOT)):
        raise HTTPException(status_code=403, detail="Circuit path is not allowed")
    text = path.read_text(encoding="utf-8")
    return render_loaded_circuit(text, item["path"], item["kind"], item["id"], item["id"], layout_for_path(path))


@app.post("/api/circuit/reload")
def reload_current() -> dict[str, Any]:
    if not CURRENT_STATE.source_text:
        raise HTTPException(status_code=404, detail="No current circuit is loaded")
    item = catalog_index().get(CURRENT_STATE.current_circuit_id or "")
    if item and CURRENT_STATE.source_kind != "uploaded":
        path = ROOT / item["path"]
        text = path.read_text(encoding="utf-8")
        return render_loaded_circuit(text, item["path"], CURRENT_STATE.source_kind, item["id"], item["id"], layout_for_path(path))
    return render_loaded_circuit(CURRENT_STATE.source_text, CURRENT_STATE.source_filename, "uploaded", CURRENT_STATE.current_circuit_id, CURRENT_STATE.case_id, CURRENT_STATE.layout_text)


@app.get("/api/circuit/current")
def current_circuit() -> dict[str, Any]:
    return CURRENT_STATE.model_dump(mode="json")


@app.post("/api/circuit/load")
def load_circuit(request: LoadRequest) -> dict[str, object]:
    path = Path(request.path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Netlist not found")
    parser = NetlistParser()
    circuit, diagnostics = parser.parse_file(path)
    return {"circuit": circuit.model_dump(mode="json") if circuit else None, "diagnostics": [d.model_dump() for d in diagnostics]}


@app.post("/api/circuit/validate")
def validate_current() -> dict[str, object]:
    rendered = build_current()
    return {"diagnostics": [d.model_dump() for d in rendered.diagnostics]}


@app.get("/api/circuit/topology")
def current_topology() -> dict[str, Any]:
    lib = library()
    parser = NetlistParser()
    if CURRENT_STATE.source_text:
        circuit, diagnostics = parser.parse_text(CURRENT_STATE.source_text, Path(CURRENT_STATE.source_filename))
    else:
        circuit, diagnostics = parser.parse_file(EXAMPLE_NETLIST)
    if circuit is None:
        return {"diagnostics": [diag.model_dump() for diag in diagnostics], "topology": None}
    diagnostics.extend(CircuitValidator(lib).validate(circuit))
    if has_blocking_diagnostics(diagnostics):
        return {"diagnostics": [diag.model_dump() for diag in diagnostics], "topology": None}
    analysis = TopologyAnalyzer().analyze(circuit, lib)
    return {"diagnostics": [diag.model_dump() for diag in diagnostics], "topology": analysis.model_dump(mode="json")}


@app.get("/api/circuit/scene")
def current_scene() -> dict[str, Any]:
    return current_scene_payload()


@app.post("/api/layout/save")
def save_layout(request: LayoutSaveRequest) -> dict[str, str]:
    CURRENT_STATE.layout_text = request.layout.model_dump_json(indent=2)
    CURRENT_STATE.layout_dirty = False
    if CURRENT_STATE.current_circuit_id == "example_solar_led":
        write_layout(request.layout)
    return {"status": "saved"}


@app.post("/api/layout/autoroute")
def autoroute(request: LayoutSaveRequest | None = None) -> dict[str, object]:
    if CURRENT_STATE.source_text:
        response = render_loaded_circuit(
            CURRENT_STATE.source_text,
            CURRENT_STATE.source_filename,
            CURRENT_STATE.source_kind,
            CURRENT_STATE.current_circuit_id,
            CURRENT_STATE.case_id,
            request.layout.model_dump_json() if request and request.layout else CURRENT_STATE.layout_text,
            update_state=False,
        )
        return {"layout": response["schematic"]["layout"], "svg": response["schematic"]["svg"], "diagnostics": response["diagnostics"]}
    rendered = build_current(request.layout if request else None)
    return {"layout": rendered.layout.model_dump(mode="json"), "svg": rendered.svg, "diagnostics": [d.model_dump() for d in rendered.diagnostics]}


@app.get("/api/export/svg")
def export_current_svg() -> Response:
    rendered = build_current()
    export_svg(rendered.svg, ROOT / "output" / "solar_led.svg")
    return Response(rendered.svg, media_type="image/svg+xml")
