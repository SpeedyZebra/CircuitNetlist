from __future__ import annotations

import hashlib
import json
from pathlib import Path
from time import perf_counter
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .component_library import ComponentLibrary, load_component_library
from .erc import ElectricalRuleChecker, can_run_electrical_rules
from .exporters import export_png_bytes_from_scene, export_png_from_scene, export_svg
from .hit_testing import hit_test_all
from .models import Circuit, Diagnostic, Layout, NetRouteStyle, RenderQualityMode, RenderedCircuit, Severity
from .parser import NetlistParser
from .placement import DeterministicPlacementEngine
from .constraint_placement import PlacementOptimizationConfig
from .regression import compare_diagnostic_codes, detect_patterns, engineering_calculations
from .rendered_connectivity import validate_rendered_connectivity
from .router import ManhattanRouter
from .scene_builder import build_schematic_scene
from .scene_renderer import render_scene_svg
from .topology import TopologyAnalyzer
from .validator import CircuitValidator, has_blocking_diagnostics
from .visual_drc import visual_drc_from_scene


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


class HitTestRequest(BaseModel):
    x: float
    y: float
    tolerance: float = 6


class NetRouteStyleRequest(BaseModel):
    net_name: str
    route_style: NetRouteStyle = NetRouteStyle.AUTO
    layout: Layout | None = None


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
    connectivity_findings: list[dict[str, Any]] = []
    expected_codes: list[str] = []
    actual_codes: list[str] = []
    expected_match: bool | None = None


app = FastAPI(title="Circuit Netlist")
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")
CURRENT_STATE = CurrentCircuitState(
    source_text=EXAMPLE_NETLIST.read_text(encoding="utf-8") if EXAMPLE_NETLIST.exists() else None,
    layout_text=EXAMPLE_LAYOUT.read_text(encoding="utf-8") if EXAMPLE_LAYOUT.exists() else None,
)
CURRENT_RENDER_CACHE: dict[str, Any] = {"key": None, "context": None, "contexts": {}}


def library() -> ComponentLibrary:
    return load_component_library(COMPONENT_ROOT)


def read_layout() -> Layout | None:
    if not EXAMPLE_LAYOUT.exists():
        return None
    return Layout.model_validate_json(EXAMPLE_LAYOUT.read_text(encoding="utf-8"))


def write_layout(layout: Layout) -> None:
    EXAMPLE_LAYOUT.write_text(layout.model_dump_json(indent=2), encoding="utf-8")


def invalidate_render_cache() -> None:
    CURRENT_RENDER_CACHE["key"] = None
    CURRENT_RENDER_CACHE["context"] = None
    CURRENT_RENDER_CACHE["contexts"] = {}


def normalized_quality(quality: RenderQualityMode | str = RenderQualityMode.INTERACTIVE) -> RenderQualityMode:
    return quality if isinstance(quality, RenderQualityMode) else RenderQualityMode(quality)


def layout_json(layout: Layout | None) -> str | None:
    return layout.model_dump_json() if layout else None


def canonical_layout_text(layout_text: str | None) -> str | None:
    layout = parse_layout_text(layout_text)
    return layout.model_dump_json() if layout else layout_text


def component_library_signature() -> str:
    parts = []
    for path in sorted(COMPONENT_ROOT.rglob("*.yaml")):
        stat = path.stat()
        parts.append(f"{path.relative_to(COMPONENT_ROOT)}:{stat.st_mtime_ns}:{stat.st_size}")
    return "|".join(parts)


def render_cache_key(source_text: str, source_filename: str, layout_text: str | None, quality: RenderQualityMode) -> str:
    payload = {
        "source": source_text,
        "filename": source_filename,
        "layout": layout_text or "",
        "quality": quality.value,
        "library": component_library_signature(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def cached_render_context(key: str) -> dict[str, Any] | None:
    contexts = CURRENT_RENDER_CACHE.get("contexts")
    if isinstance(contexts, dict):
        context = contexts.get(key)
        if isinstance(context, dict):
            return context
    if CURRENT_RENDER_CACHE.get("key") == key:
        context = CURRENT_RENDER_CACHE.get("context")
        if isinstance(context, dict):
            return context
    return None


def mark_cache_hit(context: dict[str, Any]) -> dict[str, Any]:
    context["cache_hit"] = True
    metrics = context.get("metrics", {})
    if isinstance(metrics, dict):
        timing = metrics.get("timing", {})
        if isinstance(timing, dict):
            timing["cache_hit"] = True
    return context


def store_render_context(keys: list[str], context: dict[str, Any]) -> None:
    contexts = CURRENT_RENDER_CACHE.setdefault("contexts", {})
    if not isinstance(contexts, dict):
        contexts = {}
        CURRENT_RENDER_CACHE["contexts"] = contexts
    for key in keys:
        contexts[key] = context
    while len(contexts) > 8:
        oldest_key = next(iter(contexts))
        contexts.pop(oldest_key, None)
    CURRENT_RENDER_CACHE["key"] = keys[-1] if keys else None
    CURRENT_RENDER_CACHE["context"] = context


def placement_engine_for_quality(quality: RenderQualityMode, existing_layout: Layout | None) -> DeterministicPlacementEngine:
    if quality == RenderQualityMode.INTERACTIVE:
        return DeterministicPlacementEngine(optimization_config=PlacementOptimizationConfig(mode="off"))
    return DeterministicPlacementEngine()


def router_for_quality(quality: RenderQualityMode) -> ManhattanRouter:
    return ManhattanRouter.for_quality(quality)


def render_pipeline(
    text: str,
    filename: str,
    layout_text: str | None = None,
    *,
    layout_override: Layout | None = None,
    quality: RenderQualityMode | str = RenderQualityMode.INTERACTIVE,
    use_cache: bool = True,
) -> dict[str, Any]:
    quality_mode = normalized_quality(quality)
    effective_layout_text = layout_json(layout_override) if layout_override is not None else canonical_layout_text(layout_text)
    lookup_key = render_cache_key(text, filename, effective_layout_text or "", quality_mode)
    if use_cache:
        context = cached_render_context(lookup_key)
        if context is not None:
            return mark_cache_hit(context)

    total_start = perf_counter()
    lib = library()
    parser = NetlistParser()
    parse_start = perf_counter()
    circuit, diagnostics = parser.parse_text(text, Path(filename))
    diagnostics.extend(lib.diagnostics)
    parse_time_ms = (perf_counter() - parse_start) * 1000

    validation_findings: list[Diagnostic] = []
    erc_findings: list[Diagnostic] = []
    drc_findings: list[Diagnostic] = []
    connectivity_findings: list[Diagnostic] = []
    layout = Layout()
    routes = []
    scene = None
    svg = render_error_svg(diagnostics)
    analysis = None
    patterns: list[dict[str, Any]] = []
    calculations: list[dict[str, Any]] = []
    drc_metrics: dict[str, Any] = {}
    connectivity_metrics: dict[str, Any] = {}
    route_time_ms = 0.0
    scene_time_ms = 0.0
    drc_time_ms = 0.0
    connectivity_time_ms = 0.0
    render_time_ms = 0.0
    placement_time_ms = 0.0
    layout_loaded = False
    render_allowed = circuit is not None

    if circuit is not None:
        validation_findings = CircuitValidator(lib).validate(circuit)
        diagnostics.extend(validation_findings)
        render_allowed = not has_blocking_diagnostics(diagnostics)
        analysis = TopologyAnalyzer().analyze(circuit, lib)
        if can_run_electrical_rules(validation_findings):
            erc_findings = ElectricalRuleChecker(lib).check(circuit)
            diagnostics.extend(erc_findings)
        if render_allowed:
            existing_layout = layout_override if layout_override is not None else parse_layout_text(layout_text)
            layout_loaded = existing_layout is not None
            placement_start = perf_counter()
            layout = placement_engine_for_quality(quality_mode, existing_layout).place(circuit, lib, existing_layout)
            placement_time_ms = (perf_counter() - placement_start) * 1000
            route_start = perf_counter()
            routes = router_for_quality(quality_mode).route(circuit, lib, layout)
            route_time_ms = (perf_counter() - route_start) * 1000
            for route in routes:
                for warning in route.warnings:
                    diagnostics.append(Diagnostic(severity=Severity.WARNING, code="ROUTING_WARNING", message=warning, net_name=route.name))
            scene_start = perf_counter()
            scene = build_schematic_scene(circuit, lib, layout, routes, quality=quality_mode)
            scene_time_ms = (perf_counter() - scene_start) * 1000
            drc_start = perf_counter()
            drc_findings, drc_metrics = visual_drc_from_scene(scene)
            drc_time_ms = (perf_counter() - drc_start) * 1000
            diagnostics.extend(drc_findings)
            connectivity_start = perf_counter()
            connectivity_findings, connectivity_metrics = validate_rendered_connectivity(circuit, scene)
            connectivity_time_ms = (perf_counter() - connectivity_start) * 1000
            diagnostics.extend(connectivity_findings)
            patterns = detect_patterns(circuit, lib)
            calculations = engineering_calculations(circuit, lib)
            render_start = perf_counter()
            svg = render_scene_svg(scene, diagnostics)
            render_time_ms = (perf_counter() - render_start) * 1000

    timing = {
        "quality_mode": quality_mode.value,
        "cache_hit": False,
        "parse_time_ms": round(parse_time_ms, 3),
        "placement_time_ms": round(placement_time_ms, 3),
        "route_time_ms": round(route_time_ms, 3),
        "scene_time_ms": round(scene_time_ms, 3),
        "drc_time_ms": round(drc_time_ms, 3),
        "connectivity_time_ms": round(connectivity_time_ms, 3),
        "svg_time_ms": round(render_time_ms, 3),
        "total_render_time_ms": round((perf_counter() - total_start) * 1000, 3),
    }
    if scene is not None:
        scene_timing = scene.metadata.get("timing", {})
        if isinstance(scene_timing, dict):
            timing.update(scene_timing)
    metrics = {**drc_metrics, "rendered_connectivity": connectivity_metrics, "timing": timing, "quality_mode": quality_mode.value}
    context = {
        "library": lib,
        "circuit": circuit,
        "diagnostics": diagnostics,
        "validation": validation_findings,
        "erc": erc_findings,
        "drc": drc_findings,
        "connectivity": connectivity_findings,
        "layout": layout,
        "routes": routes,
        "scene": scene,
        "svg": svg,
        "analysis": analysis,
        "patterns": patterns,
        "calculations": calculations,
        "metrics": metrics,
        "render_allowed": render_allowed,
        "layout_loaded": layout_loaded,
        "quality_mode": quality_mode,
        "cache_hit": False,
    }
    final_layout_text = layout.model_dump_json() if circuit is not None else (effective_layout_text or "")
    store_key = render_cache_key(text, filename, final_layout_text, quality_mode)
    if use_cache:
        store_keys = [lookup_key]
        if store_key != lookup_key:
            store_keys.append(store_key)
        store_render_context(store_keys, context)
    return context


def response_from_context(
    context: dict[str, Any],
    filename: str,
    source_kind: str,
    circuit_id: str,
    case_id: str | None,
    expected_codes: list[str],
) -> dict[str, Any]:
    circuit = context.get("circuit")
    diagnostics: list[Diagnostic] = context.get("diagnostics", [])
    validation_findings: list[Diagnostic] = context.get("validation", [])
    erc_findings: list[Diagnostic] = context.get("erc", [])
    drc_findings: list[Diagnostic] = context.get("drc", [])
    connectivity_findings: list[Diagnostic] = context.get("connectivity", [])
    layout: Layout = context.get("layout") or Layout()
    scene = context.get("scene")
    actual_codes = sorted({diag.code for diag in diagnostics if diag.code})
    expected_match = compare_expected_codes(expected_codes, actual_codes) if case_id else None
    success = circuit is not None and not has_blocking_diagnostics(validation_findings)
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
        "connectivity": [diag.model_dump(mode="json") for diag in connectivity_findings],
        "diagnostics": [diag.model_dump(mode="json") for diag in diagnostics],
        "render_allowed": context.get("render_allowed", False),
        "schematic": {
            "svg": context.get("svg", ""),
            "layout": layout.model_dump(mode="json"),
            "circuit": circuit.model_dump(mode="json") if circuit else None,
            "scene": scene.model_dump(mode="json") if scene else None,
        },
        "layout_loaded": context.get("layout_loaded", False),
        "expected": {"codes": expected_codes, "actual_codes": actual_codes, "match": expected_match},
        "patterns": context.get("patterns", []),
        "calculations": context.get("calculations", []),
        "metrics": context.get("metrics", {}),
    }


def build_current(layout_override: Layout | None = None, use_saved_layout: bool = True, quality: RenderQualityMode | str = RenderQualityMode.INTERACTIVE) -> RenderedCircuit:
    saved_layout = read_layout() if use_saved_layout else None
    layout_text = saved_layout.model_dump_json() if saved_layout else None
    context = render_pipeline(
        EXAMPLE_NETLIST.read_text(encoding="utf-8"),
        str(EXAMPLE_NETLIST),
        layout_text,
        layout_override=layout_override,
        quality=quality,
        use_cache=False,
    )
    return RenderedCircuit(svg=context["svg"], diagnostics=context["diagnostics"], layout=context["layout"], circuit=context["circuit"])


def render_loaded_circuit(
    text: str,
    filename: str,
    source_kind: str,
    circuit_id: str,
    case_id: str | None = None,
    layout_text: str | None = None,
    update_state: bool = True,
    quality: RenderQualityMode | str = RenderQualityMode.INTERACTIVE,
) -> dict[str, Any]:
    expected_codes = expected_codes_for_case(case_id)
    context = render_pipeline(text, filename, layout_text, quality=quality)
    response = response_from_context(context, filename, source_kind, circuit_id, case_id, expected_codes)
    success = bool(response["success"])
    if update_state and success:
        circuit: Circuit = context["circuit"]
        layout: Layout = context["layout"]
        CURRENT_STATE.current_circuit_id = circuit_id
        CURRENT_STATE.current_circuit_name = circuit.name
        CURRENT_STATE.source_kind = source_kind
        CURRENT_STATE.source_filename = filename
        CURRENT_STATE.source_text = text
        CURRENT_STATE.layout_text = layout.model_dump_json(indent=2)
        CURRENT_STATE.case_id = case_id
        CURRENT_STATE.layout_dirty = False
        CURRENT_STATE.validation_findings = response["validation"]
        CURRENT_STATE.erc_findings = response["erc"]
        CURRENT_STATE.drc_findings = response["drc"]
        CURRENT_STATE.connectivity_findings = response["connectivity"]
        CURRENT_STATE.expected_codes = expected_codes
        CURRENT_STATE.actual_codes = response["expected"]["actual_codes"]
        CURRENT_STATE.expected_match = response["expected"]["match"]
    elif success:
        remember_generated_layout(context, layout_text, None)
    return response


def remember_generated_layout(context: dict[str, Any], incoming_layout_text: str | None, layout_override: Layout | None) -> None:
    if layout_override is not None or incoming_layout_text or CURRENT_STATE.layout_dirty:
        return
    circuit = context.get("circuit")
    layout = context.get("layout")
    if circuit is None or not isinstance(layout, Layout):
        return
    CURRENT_STATE.layout_text = layout.model_dump_json(indent=2)
    CURRENT_STATE.layout_dirty = False


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


def current_scene_payload(quality: RenderQualityMode | str = RenderQualityMode.INTERACTIVE) -> dict[str, Any]:
    context = current_render_context(quality=quality)
    diagnostics = context["diagnostics"]
    scene = context.get("scene")
    layout = context.get("layout")
    routes = context.get("routes", [])
    return {
        "scene": scene.model_dump(mode="json") if scene else None,
        "diagnostics": [diag.model_dump(mode="json") for diag in diagnostics],
        "layout": layout.model_dump(mode="json") if layout else {},
        "route_count": len(routes),
        "metrics": context.get("metrics", {}),
        "quality_mode": normalized_quality(quality).value,
        "cache_hit": bool(context.get("cache_hit")),
    }


def current_render_context(layout_override: Layout | None = None, quality: RenderQualityMode | str = RenderQualityMode.INTERACTIVE) -> dict[str, Any]:
    source_text = CURRENT_STATE.source_text or (EXAMPLE_NETLIST.read_text(encoding="utf-8") if EXAMPLE_NETLIST.exists() else "")
    source_filename = CURRENT_STATE.source_filename or str(EXAMPLE_NETLIST)
    layout_text = CURRENT_STATE.layout_text
    context = render_pipeline(
        source_text,
        source_filename,
        layout_text,
        layout_override=layout_override,
        quality=quality,
        use_cache=layout_override is None,
    )
    remember_generated_layout(context, layout_text, layout_override)
    return context


def component_detail_payload(ref: str, context: dict[str, Any]) -> dict[str, Any]:
    circuit: Circuit | None = context.get("circuit")
    lib: ComponentLibrary = context["library"]
    layout: Layout | None = context.get("layout")
    analysis = context.get("analysis")
    if circuit is None:
        raise HTTPException(status_code=404, detail="No circuit is loaded")
    component = next((item for item in circuit.components if item.ref == ref), None)
    if component is None:
        raise HTTPException(status_code=404, detail="Component not found")
    definition = lib.get(component.component_id)
    pin_nets = connected_pin_nets(circuit)
    placement = layout.components.get(ref) if layout else None
    metadata = dict(definition.metadata) if definition else {}
    pin_metadata = metadata.get("pins", {}) if isinstance(metadata.get("pins", {}), dict) else {}
    pins = []
    if definition:
        for pin in definition.pins:
            info = pin_metadata.get(pin.name) or pin_metadata.get(pin.number) or {}
            pins.append(
                {
                    "number": pin.number,
                    "name": pin.name,
                    "electrical_type": pin.electrical_type.value,
                    "connected_net": pin_nets.get((ref, pin.name)) or pin_nets.get((ref, pin.number)),
                    "description": info.get("description", ""),
                    "expected_connection": info.get("expected_connection", ""),
                }
            )
    patterns = []
    if analysis:
        for pattern in analysis.patterns:
            if ref in pattern.component_refs:
                patterns.append({"type": pattern.pattern_type.value, "confidence": pattern.confidence, "metadata": pattern.metadata})
    return {
        "ref": ref,
        "component_id": component.component_id,
        "name": definition.name if definition else component.component_id,
        "category": definition.category if definition else "",
        "package": definition.package if definition else "",
        "parameters": {name: getattr(value, "original", value) for name, value in component.parameters.items()},
        "value": getattr(component.parameters.get("value"), "original", None),
        "role": getattr(component.parameters.get("role"), "original", component.parameters.get("role")),
        "orientation": placement.rotation if placement else None,
        "verified_status": metadata.get("verified_status", "unknown" if not definition else "unspecified"),
        "summary": metadata.get("summary") or (definition.description if definition else "Unknown component definition."),
        "function": metadata.get("function", ""),
        "common_use": metadata.get("common_use", ""),
        "notes": metadata.get("notes", ""),
        "warnings": metadata.get("warnings", ""),
        "datasheet_url": metadata.get("datasheet_url", ""),
        "pins": pins,
        "topology_role": analysis.component_roles.get(ref).value if analysis and ref in analysis.component_roles else None,
        "topology_patterns": patterns,
        "generic": bool(metadata.get("generic_symbol") or metadata.get("functional_symbol") or metadata.get("incomplete_pinout")),
    }


def net_detail_payload(net_name: str, context: dict[str, Any]) -> dict[str, Any]:
    circuit: Circuit | None = context.get("circuit")
    layout: Layout | None = context.get("layout")
    routes: list[Any] = context.get("routes", [])
    if circuit is None:
        raise HTTPException(status_code=404, detail="No circuit is loaded")
    net = next((item for item in circuit.nets if item.name == net_name), None)
    if net is None:
        raise HTTPException(status_code=404, detail="Net not found")
    route = next((item for item in routes if item.name == net_name), None)
    manual = (layout.nets.get(net_name, {}) if layout else {}).get("route_style")
    route_meta = route.metadata.get("route_style", {}) if route else {}
    return {
        "net_name": net_name,
        "endpoint_count": len(net.pins),
        "manual_override": manual,
        "resolved_route_style": route_meta.get("selected_style") or _route_style_from_render_style(route.render_style if route else ""),
        "render_style": route.render_style if route else "",
        "auto_reason": route_meta.get("reason", ""),
        "route_style_debug": route_meta,
        "available_route_styles": [style.value for style in NetRouteStyle],
        "endpoints": route.endpoints if route else [],
    }


def connected_pin_nets(circuit: Circuit) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    for net in circuit.nets:
        for pin in net.pins:
            result[(pin.component_ref, pin.pin_name)] = net.name
            if pin.resolved_name:
                result[(pin.component_ref, pin.resolved_name)] = net.name
            if pin.resolved_number:
                result[(pin.component_ref, pin.resolved_number)] = net.name
    return result


def apply_net_route_style(layout: Layout, net_name: str, route_style: NetRouteStyle) -> Layout:
    data = layout.nets.setdefault(net_name, {})
    if route_style == NetRouteStyle.AUTO:
        data.pop("route_style", None)
        data.pop("render_style", None)
        if not data:
            layout.nets.pop(net_name, None)
    else:
        data["route_style"] = route_style.value
        data.pop("render_style", None)
    return layout


def _route_style_from_render_style(render_style: str) -> str:
    if render_style == "power_symbol":
        return NetRouteStyle.POWER_SYMBOL.value
    if render_style == "net_label":
        return NetRouteStyle.LABEL.value
    if render_style == "local_wire":
        return NetRouteStyle.DIRECT.value
    return NetRouteStyle.AUTO.value


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
def get_circuit(fresh: bool = False, quality: RenderQualityMode = RenderQualityMode.INTERACTIVE) -> dict[str, object]:
    if fresh:
        invalidate_render_cache()
    if CURRENT_STATE.source_text and not fresh:
        response = render_loaded_circuit(
            CURRENT_STATE.source_text,
            CURRENT_STATE.source_filename,
            CURRENT_STATE.source_kind,
            CURRENT_STATE.current_circuit_id,
            CURRENT_STATE.case_id,
            CURRENT_STATE.layout_text,
            update_state=False,
            quality=quality,
        )
        schematic = response["schematic"]
        return {**schematic, "diagnostics": response["diagnostics"], "current": CURRENT_STATE.model_dump(mode="json"), "expected": response["expected"]}
    rendered = build_current(use_saved_layout=not fresh, quality=quality)
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
    invalidate_render_cache()
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
    invalidate_render_cache()
    return render_loaded_circuit(text, item["path"], item["kind"], item["id"], item["id"], layout_for_path(path))


@app.post("/api/circuit/reload")
def reload_current() -> dict[str, Any]:
    if not CURRENT_STATE.source_text:
        raise HTTPException(status_code=404, detail="No current circuit is loaded")
    item = catalog_index().get(CURRENT_STATE.current_circuit_id or "")
    if item and CURRENT_STATE.source_kind != "uploaded":
        path = ROOT / item["path"]
        text = path.read_text(encoding="utf-8")
        invalidate_render_cache()
        return render_loaded_circuit(text, item["path"], CURRENT_STATE.source_kind, item["id"], item["id"], layout_for_path(path))
    invalidate_render_cache()
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
    rendered = build_current(quality=RenderQualityMode.STRICT)
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


@app.get("/api/circuit/placement-score")
def current_placement_score(quality: RenderQualityMode = RenderQualityMode.INTERACTIVE) -> dict[str, Any]:
    payload = current_scene_payload(quality=quality)
    layout = payload.get("layout") or {}
    canvas = layout.get("canvas", {}) if isinstance(layout, dict) else {}
    return {
        "diagnostics": payload.get("diagnostics", []),
        "placement_optimizer": canvas.get("placement_optimizer"),
        "layout_available": bool(layout),
        "quality_mode": payload.get("quality_mode"),
        "metrics": payload.get("metrics", {}),
    }


@app.get("/api/circuit/scene")
def current_scene(quality: RenderQualityMode = RenderQualityMode.INTERACTIVE) -> dict[str, Any]:
    return current_scene_payload(quality=quality)


@app.get("/api/circuit/component-details/{ref}")
def current_component_details(ref: str, quality: RenderQualityMode = RenderQualityMode.INTERACTIVE) -> dict[str, Any]:
    return component_detail_payload(ref, current_render_context(quality=quality))


@app.get("/api/circuit/net-details/{net_name}")
def current_net_details(net_name: str, quality: RenderQualityMode = RenderQualityMode.INTERACTIVE) -> dict[str, Any]:
    return net_detail_payload(net_name, current_render_context(quality=quality))


@app.post("/api/circuit/scene/hit-test")
def current_scene_hit_test(request: HitTestRequest) -> dict[str, Any]:
    payload = current_scene_payload()
    if not payload.get("scene"):
        return {"hits": [], "diagnostics": payload.get("diagnostics", [])}
    from .scene import SchematicScene

    scene = SchematicScene.model_validate(payload["scene"])
    hits = hit_test_all(scene, request.x, request.y, request.tolerance)
    return {
        "hits": [
            {
                "scene_id": hit.id,
                "kind": hit.kind,
                "owner": hit.owner_id or hit.parent_id,
                "component_ref": hit.component_ref,
                "pin_name": hit.pin_name,
                "pin_number": hit.pin_number,
                "net_name": hit.net_name,
                "z_index": hit.z_index,
                "priority": index,
            }
            for index, hit in enumerate(hits)
        ]
    }


@app.post("/api/layout/save")
def save_layout(request: LayoutSaveRequest) -> dict[str, str]:
    CURRENT_STATE.layout_text = request.layout.model_dump_json(indent=2)
    CURRENT_STATE.layout_dirty = False
    invalidate_render_cache()
    if CURRENT_STATE.current_circuit_id == "example_solar_led":
        write_layout(request.layout)
    return {"status": "saved"}


@app.post("/api/layout/autoroute")
def autoroute(request: LayoutSaveRequest | None = None) -> dict[str, object]:
    source_text = CURRENT_STATE.source_text or (EXAMPLE_NETLIST.read_text(encoding="utf-8") if EXAMPLE_NETLIST.exists() else "")
    source_filename = CURRENT_STATE.source_filename or str(EXAMPLE_NETLIST)
    layout_text = request.layout.model_dump_json(indent=2) if request and request.layout else CURRENT_STATE.layout_text
    CURRENT_STATE.layout_text = layout_text
    CURRENT_STATE.layout_dirty = True
    context = render_pipeline(source_text, source_filename, layout_text, quality=RenderQualityMode.INTERACTIVE, use_cache=False)
    response = response_from_context(context, source_filename, CURRENT_STATE.source_kind, CURRENT_STATE.current_circuit_id, CURRENT_STATE.case_id, CURRENT_STATE.expected_codes)
    return {
        "layout": response["schematic"]["layout"],
        "scene": response["schematic"].get("scene"),
        "svg": response["schematic"]["svg"],
        "diagnostics": response["diagnostics"],
        "schematic": response["schematic"],
        "current": CURRENT_STATE.model_dump(mode="json"),
        "expected": response["expected"],
        "metrics": response["metrics"],
    }


@app.post("/api/layout/net-route-style")
def set_net_route_style(request: NetRouteStyleRequest) -> dict[str, object]:
    layout = request.layout or parse_layout_text(CURRENT_STATE.layout_text)
    if layout is None:
        context = current_render_context()
        layout = context.get("layout") or Layout()
    apply_net_route_style(layout, request.net_name, request.route_style)
    CURRENT_STATE.layout_text = layout.model_dump_json(indent=2)
    CURRENT_STATE.layout_dirty = True
    invalidate_render_cache()
    return autoroute(LayoutSaveRequest(layout=layout))


@app.get("/api/export/svg")
def export_current_svg() -> Response:
    context = current_render_context()
    export_svg(context["svg"], ROOT / "output" / "solar_led.svg")
    return Response(context["svg"], media_type="image/svg+xml")


@app.get("/api/export/png")
def export_current_png() -> Response:
    payload = current_scene_payload()
    if not payload.get("scene"):
        raise HTTPException(status_code=400, detail="No renderable scene is available")
    from .scene import SchematicScene

    scene = SchematicScene.model_validate(payload["scene"])
    try:
        png_bytes = export_png_bytes_from_scene(scene)
        export_png_from_scene(scene, ROOT / "output" / "solar_led.png")
    except RuntimeError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return Response(png_bytes, media_type="image/png")
