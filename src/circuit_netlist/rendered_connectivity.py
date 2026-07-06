from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from math import hypot
from typing import Any

from .geometry import segment_crosses_box, segments_intersect
from .models import Circuit, Diagnostic, Net, PinRef, Severity
from .scene import Bounds, SceneElement, SchematicScene


CONNECTION_TOLERANCE_PX = 2.0
ROUTE_ELEMENT_KINDS = {
    "wire",
    "wire_stub",
    "junction",
    "net_label",
    "net_label_endpoint",
    "power_label",
    "ground_label",
    "power_symbol",
    "ground_symbol",
}


@dataclass(frozen=True)
class RenderedConnectionNode:
    id: str
    kind: str
    net_name: str | None
    component_ref: str | None = None
    pin_name: str | None = None
    point: tuple[float, float] | None = None
    scene_element_id: str | None = None


@dataclass(frozen=True)
class RenderedConnectionEdge:
    node_a: str
    node_b: str
    kind: str
    net_name: str | None
    scene_element_ids: tuple[str, ...] = ()


@dataclass
class RenderedConnectivityGraph:
    nodes: list[RenderedConnectionNode] = field(default_factory=list)
    edges: list[RenderedConnectionEdge] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class _SegmentInfo:
    element: SceneElement
    net_name: str
    segment: tuple[int, int, int, int]
    start_node: str
    end_node: str
    wire_kind: str
    external_contacts: dict[str, int] = field(default_factory=lambda: {"start": 0, "end": 0})


@dataclass
class _PinInfo:
    element: SceneElement
    point: tuple[float, float]
    expected_net: str | None
    node_id: str
    expected_pin_id: str | None = None
    contact_count: int = 0


@dataclass
class _AnchorInfo:
    element: SceneElement
    net_name: str
    node_id: str
    kind: str
    box: tuple[float, float, float, float]
    contact_count: int = 0


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, item: str) -> None:
        self.parent.setdefault(item, item)

    def find(self, item: str) -> str:
        parent = self.parent.setdefault(item, item)
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, a: str, b: str) -> None:
        root_a = self.find(a)
        root_b = self.find(b)
        if root_a != root_b:
            self.parent[root_b] = root_a

    def copy(self) -> "_UnionFind":
        copied = _UnionFind()
        copied.parent = dict(self.parent)
        return copied


class _RenderedConnectivityBuilder:
    def __init__(self, circuit: Circuit, scene: SchematicScene, tolerance: float = CONNECTION_TOLERANCE_PX) -> None:
        self.circuit = circuit
        self.scene = scene
        self.tolerance = tolerance
        self.graph = RenderedConnectivityGraph()
        self.uf = _UnionFind()
        self.diagnostics: list[Diagnostic] = []
        self.expected_nets = {net.name for net in circuit.nets}
        self.expected_pin_nets, self.expected_pin_ids_by_net = _expected_pin_maps(circuit)
        self.net_by_pin_key = {key: net_name for net_name, keys in self.expected_pin_nets.items() for key in keys}
        self.net_by_expected_pin_id = {pin_id: net.name for net in circuit.nets for pin_id in [_pin_id(pin) for pin in net.pins]}
        self.segments: list[_SegmentInfo] = []
        self.pins: list[_PinInfo] = []
        self.anchors: list[_AnchorInfo] = []
        self.pin_nodes_by_expected_id: dict[str, str] = {}
        self.route_styles: dict[str, str] = {}

    def build(self) -> tuple[list[Diagnostic], RenderedConnectivityGraph]:
        self._collect_route_styles()
        self._collect_pins()
        self._collect_route_elements()
        self._connect_same_net_geometry()
        self._detect_wrong_net_contacts()
        physical_roots = self._net_roots(self.uf)
        logical_uf = self.uf.copy()
        self._connect_label_and_symbol_equivalence(logical_uf)
        logical_roots = self._net_roots(logical_uf)
        self._validate_expected_pins(logical_uf)
        self._validate_anchors()
        self._validate_segments()
        self._validate_net_islands(physical_roots, logical_roots)
        self.graph.metrics = self._metrics(physical_roots, logical_roots)
        return _dedupe(self.diagnostics), self.graph

    def _collect_route_styles(self) -> None:
        for element in self.scene.elements:
            if element.kind == "net_group" and element.net_name:
                self.route_styles[element.net_name] = str(element.metadata.get("render_style", ""))

    def _collect_pins(self) -> None:
        for element in self.scene.elements:
            if element.kind != "pin" or not element.component_ref:
                continue
            point = _pin_center(element)
            keys = _scene_pin_keys(element)
            expected_net = next((self.net_by_pin_key[key] for key in keys if key in self.net_by_pin_key), None)
            expected_pin_id = next((pin_id for pin_id, net_name in self.net_by_expected_pin_id.items() if net_name == expected_net and _pin_id_matches_scene(pin_id, element)), None)
            node_id = self._add_node(
                kind="pin",
                net_name=expected_net,
                component_ref=element.component_ref,
                pin_name=element.pin_name,
                point=point,
                scene_element_id=element.id,
            )
            info = _PinInfo(element=element, point=point, expected_net=expected_net, node_id=node_id, expected_pin_id=expected_pin_id)
            self.pins.append(info)
            if expected_pin_id:
                self.pin_nodes_by_expected_id[expected_pin_id] = node_id

    def _collect_route_elements(self) -> None:
        for element in self.scene.elements:
            if element.kind in ROUTE_ELEMENT_KINDS and (not element.net_name or element.net_name not in self.expected_nets):
                self.diagnostics.append(
                    _diag(
                        Severity.ERROR,
                        "ROUTING_ROUTE_NET_UNKNOWN",
                        f"Rendered {element.kind} {element.id} has unknown net {element.net_name or '<missing>'}",
                        net_name=element.net_name,
                        metadata={"scene_element_id": element.id},
                    )
                )
            if element.kind in {"wire", "wire_stub"}:
                self._collect_segment(element)
            elif element.kind == "junction" and element.net_name:
                self._collect_junction(element)
            elif element.kind == "net_label_endpoint" and element.net_name:
                self._collect_anchor(element, "label_anchor")
            elif element.kind in {"power_symbol", "ground_symbol"} and element.net_name:
                self._collect_anchor(element, "power_symbol_anchor")
            elif element.kind in {"net_label", "power_label", "ground_label"} and element.text and element.net_name:
                self._validate_label_text(element)

    def _collect_segment(self, element: SceneElement) -> None:
        segment = _line_segment_from_element(element)
        if not segment:
            return
        net_name = element.net_name or ""
        start_node = self._add_node("wire_endpoint", net_name, point=(segment[0], segment[1]), scene_element_id=element.id)
        end_node = self._add_node("wire_endpoint", net_name, point=(segment[2], segment[3]), scene_element_id=element.id)
        self._connect(start_node, end_node, "stub_continuity" if element.kind == "wire_stub" else "wire_continuity", net_name, (element.id,))
        self.segments.append(
            _SegmentInfo(
                element=element,
                net_name=net_name,
                segment=segment,
                start_node=start_node,
                end_node=end_node,
                wire_kind=str(element.metadata.get("wire_kind", "wire")),
            )
        )

    def _collect_junction(self, element: SceneElement) -> None:
        point = _element_center(element)
        node_id = self._add_node("junction", element.net_name, point=point, scene_element_id=element.id)
        for segment in self.segments:
            if segment.net_name == element.net_name and _point_on_segment(point, segment.segment, self.tolerance):
                self._connect(node_id, segment.start_node, "wire_continuity", element.net_name, (element.id, segment.element.id))
                segment.external_contacts["start"] += 1
                segment.external_contacts["end"] += 1

    def _collect_anchor(self, element: SceneElement, kind: str) -> None:
        node_id = self._add_node(kind, element.net_name, point=_element_center(element), scene_element_id=element.id)
        self.anchors.append(_AnchorInfo(element=element, net_name=element.net_name or "", node_id=node_id, kind=kind, box=element.active_collision_bounds().as_tuple()))

    def _connect_same_net_geometry(self) -> None:
        for pin in self.pins:
            if not pin.expected_net:
                continue
            for segment in self.segments:
                if segment.net_name != pin.expected_net:
                    continue
                if _point_on_segment(pin.point, segment.segment, self.tolerance):
                    self._connect(pin.node_id, _nearest_segment_endpoint_node(pin.point, segment), "pin_contact", pin.expected_net, (pin.element.id, segment.element.id))
                    pin.contact_count += 1
                    _mark_nearest_endpoint_contact(pin.point, segment)

        for anchor in self.anchors:
            for segment in self.segments:
                if segment.net_name != anchor.net_name:
                    continue
                endpoint_node = _segment_endpoint_touching_box(segment, anchor.box, self.tolerance)
                if endpoint_node:
                    self._connect(anchor.node_id, endpoint_node, "physical_contact", anchor.net_name, (anchor.element.id, segment.element.id))
                    anchor.contact_count += 1
                    if endpoint_node == segment.start_node:
                        segment.external_contacts["start"] += 1
                    else:
                        segment.external_contacts["end"] += 1

        for index, first in enumerate(self.segments):
            for second in self.segments[index + 1 :]:
                if first.net_name != second.net_name:
                    continue
                contacts = _same_net_segment_contacts(first, second, self.tolerance)
                for node_a, node_b in contacts:
                    self._connect(node_a, node_b, "wire_continuity", first.net_name, (first.element.id, second.element.id))

    def _detect_wrong_net_contacts(self) -> None:
        for index, first in enumerate(self.segments):
            for second in self.segments[index + 1 :]:
                if not first.net_name or not second.net_name or first.net_name == second.net_name:
                    continue
                if not segments_intersect(first.segment, second.segment):
                    continue
                code = "DRC_UNRELATED_STUB_CONTACT" if "stub" in {first.element.kind, second.element.kind} else "DRC_RENDERED_NET_SHORT"
                self.diagnostics.append(
                    _diag(
                        Severity.ERROR,
                        code,
                        f"Rendered {first.net_name} touches {second.net_name}",
                        net_name=first.net_name,
                        metadata={"shorted_to_net": second.net_name, "scene_element_ids": [first.element.id, second.element.id], "subsystem": "rendered_connectivity"},
                    )
                )
                if code != "DRC_RENDERED_NET_SHORT":
                    self.diagnostics.append(
                        _diag(
                            Severity.ERROR,
                            "DRC_RENDERED_NET_SHORT",
                            f"Rendered {first.net_name} touches {second.net_name}",
                            net_name=first.net_name,
                            metadata={"shorted_to_net": second.net_name, "scene_element_ids": [first.element.id, second.element.id], "subsystem": "rendered_connectivity"},
                        )
                    )

        for pin in self.pins:
            if not pin.expected_net:
                continue
            for segment in self.segments:
                if segment.net_name == pin.expected_net:
                    continue
                if _point_on_segment(pin.point, segment.segment, self.tolerance):
                    self.diagnostics.append(
                        _diag(
                            Severity.ERROR,
                            "DRC_PIN_WRONG_NET_CONTACT",
                            f"{pin.element.component_ref}.{pin.element.pin_name or pin.element.pin_number} touches rendered net {segment.net_name}",
                            component_ref=pin.element.component_ref,
                            pin_ref=pin.element.pin_name or pin.element.pin_number,
                            net_name=pin.expected_net,
                            metadata={"shorted_to_net": segment.net_name, "scene_element_ids": [pin.element.id, segment.element.id]},
                        )
                    )
                    self.diagnostics.append(
                        _diag(
                            Severity.ERROR,
                            "ROUTING_EXTRA_PIN_ON_NET",
                            f"Rendered net {segment.net_name} touches extra pin {pin.element.component_ref}.{pin.element.pin_name or pin.element.pin_number}",
                            component_ref=pin.element.component_ref,
                            pin_ref=pin.element.pin_name or pin.element.pin_number,
                            net_name=segment.net_name,
                            metadata={"expected_net": pin.expected_net, "scene_element_ids": [pin.element.id, segment.element.id]},
                        )
                    )

        for anchor in self.anchors:
            for segment in self.segments:
                if segment.net_name == anchor.net_name:
                    continue
                if segment_crosses_box(segment.segment, anchor.box) or _segment_endpoint_touching_box(segment, anchor.box, self.tolerance):
                    code = "DRC_LABEL_WRONG_NET_CONTACT" if anchor.kind == "label_anchor" else "DRC_POWER_SYMBOL_WRONG_NET_CONTACT"
                    self.diagnostics.append(
                        _diag(
                            Severity.ERROR,
                            code,
                            f"{anchor.net_name} {anchor.kind.replace('_', ' ')} touches rendered net {segment.net_name}",
                            net_name=anchor.net_name,
                            metadata={"shorted_to_net": segment.net_name, "scene_element_ids": [anchor.element.id, segment.element.id]},
                        )
                    )

    def _connect_label_and_symbol_equivalence(self, logical_uf: _UnionFind) -> None:
        by_kind_and_net: dict[tuple[str, str], list[_AnchorInfo]] = defaultdict(list)
        for anchor in self.anchors:
            by_kind_and_net[(anchor.kind, anchor.net_name)].append(anchor)
        for (kind, net_name), anchors in by_kind_and_net.items():
            if len(anchors) < 2:
                continue
            edge_kind = "label_equivalence" if kind == "label_anchor" else "power_symbol_equivalence"
            first = anchors[0]
            for other in anchors[1:]:
                logical_uf.union(first.node_id, other.node_id)
                self.graph.edges.append(RenderedConnectionEdge(first.node_id, other.node_id, edge_kind, net_name, (first.element.id, other.element.id)))

    def _validate_expected_pins(self, logical_uf: _UnionFind) -> None:
        for net in self.circuit.nets:
            for pin in net.pins:
                pin_id = _pin_id(pin)
                node_id = self.pin_nodes_by_expected_id.get(pin_id)
                if node_id is None:
                    self.diagnostics.append(
                        _diag(
                            Severity.ERROR,
                            "ROUTING_RENDERED_PIN_MISSING",
                            f"Expected pin {pin_id} for net {net.name} has no rendered pin anchor",
                            component_ref=pin.component_ref,
                            pin_ref=pin.resolved_name or pin.pin_name,
                            net_name=net.name,
                        )
                    )
                    continue
                pin_info = next((item for item in self.pins if item.node_id == node_id), None)
                if pin_info and pin_info.contact_count == 0 and not net.allow_single:
                    self.diagnostics.append(
                        _diag(
                            Severity.ERROR,
                            "ROUTING_PIN_UNREACHED",
                            f"Expected pin {pin_id} is not connected to rendered net {net.name}",
                            component_ref=pin.component_ref,
                            pin_ref=pin.resolved_name or pin.pin_name,
                            net_name=net.name,
                        )
                    )
            pin_nodes = [self.pin_nodes_by_expected_id[_pin_id(pin)] for pin in net.pins if _pin_id(pin) in self.pin_nodes_by_expected_id]
            if len(pin_nodes) > 1 and len({logical_uf.find(node) for node in pin_nodes}) > 1:
                self.diagnostics.append(
                    _diag(
                        Severity.ERROR,
                        "DRC_RENDERED_NET_OPEN",
                        f"Rendered net {net.name} does not connect all expected pins",
                        net_name=net.name,
                    )
                )

    def _validate_anchors(self) -> None:
        for anchor in self.anchors:
            if anchor.contact_count:
                continue
            if anchor.kind == "label_anchor":
                self.diagnostics.append(
                    _diag(
                        Severity.ERROR,
                        "ROUTING_LABEL_WITHOUT_STUB",
                        f"Net label for {anchor.net_name} has no connected stub",
                        net_name=anchor.net_name,
                        metadata={"scene_element_id": anchor.element.id},
                    )
                )
            else:
                self.diagnostics.append(
                    _diag(
                        Severity.ERROR,
                        "ROUTING_POWER_SYMBOL_WITHOUT_STUB",
                        f"Power symbol for {anchor.net_name} has no connected stub",
                        net_name=anchor.net_name,
                        metadata={"scene_element_id": anchor.element.id},
                    )
                )

    def _validate_segments(self) -> None:
        for segment in self.segments:
            if not segment.net_name:
                continue
            missing_contacts = [name for name, count in segment.external_contacts.items() if count == 0]
            if not missing_contacts:
                continue
            if segment.element.kind == "wire_stub":
                self.diagnostics.append(
                    _diag(
                        Severity.ERROR,
                        "ROUTING_STUB_DISCONNECTED",
                        f"Rendered stub for net {segment.net_name} has a disconnected endpoint",
                        net_name=segment.net_name,
                        metadata={"scene_element_id": segment.element.id, "endpoints": missing_contacts},
                    )
                )
            elif len(missing_contacts) == 2:
                self.diagnostics.append(
                    _diag(
                        Severity.ERROR,
                        "ROUTING_DANGLING_SEGMENT",
                        f"Rendered wire segment for net {segment.net_name} is orphaned",
                        net_name=segment.net_name,
                        metadata={"scene_element_id": segment.element.id},
                    )
                )
            else:
                self.diagnostics.append(
                    _diag(
                        Severity.ERROR,
                        "ROUTING_DISCONNECTED_SEGMENT",
                        f"Rendered wire segment for net {segment.net_name} has a disconnected endpoint",
                        net_name=segment.net_name,
                        metadata={"scene_element_id": segment.element.id, "endpoints": missing_contacts},
                    )
                )

    def _validate_label_text(self, element: SceneElement) -> None:
        label = (element.text.text if element.text else "").strip()
        if not label or label == element.net_name:
            return
        code = "ROUTING_LABEL_NET_MISMATCH" if element.kind == "net_label" else "ROUTING_POWER_SYMBOL_NET_MISMATCH"
        self.diagnostics.append(
            _diag(
                Severity.ERROR,
                code,
                f"Rendered label text {label} does not match net {element.net_name}",
                net_name=element.net_name,
                metadata={"scene_element_id": element.id, "label_text": label},
            )
        )

    def _validate_net_islands(self, physical_roots: dict[str, set[str]], logical_roots: dict[str, set[str]]) -> None:
        nets_by_name = {net.name: net for net in self.circuit.nets}
        for net_name in sorted(self.expected_nets):
            net = nets_by_name[net_name]
            logical_count = len(logical_roots.get(net_name, set()))
            if logical_count > 1 and not (net.allow_single and len(net.pins) <= 1):
                self.diagnostics.append(
                    _diag(
                        Severity.ERROR,
                        "ROUTING_NET_HAS_MULTIPLE_ISLANDS",
                        f"Rendered net {net_name} has {logical_count} disconnected logical islands",
                        net_name=net_name,
                        metadata={"logical_island_count": logical_count},
                    )
                )
            physical_count = len(physical_roots.get(net_name, set()))
            if self.route_styles.get(net_name) == "local_wire" and physical_count > 1 and not net.allow_single:
                self.diagnostics.append(
                    _diag(
                        Severity.ERROR,
                        "ROUTING_NET_INCOMPLETE",
                        f"Direct-wire net {net_name} has {physical_count} disconnected physical islands",
                        net_name=net_name,
                        metadata={"physical_island_count": physical_count},
                    )
                )

    def _net_roots(self, uf: _UnionFind) -> dict[str, set[str]]:
        roots: dict[str, set[str]] = defaultdict(set)
        for node in self.graph.nodes:
            if not node.net_name or node.net_name not in self.expected_nets:
                continue
            roots[node.net_name].add(uf.find(node.id))
        return roots

    def _metrics(self, physical_roots: dict[str, set[str]], logical_roots: dict[str, set[str]]) -> dict[str, Any]:
        by_net: dict[str, dict[str, Any]] = {}
        for net in self.circuit.nets:
            pins = [_pin_id(pin) for pin in net.pins]
            rendered_pins = sorted(pin_id for pin_id in pins if pin_id in self.pin_nodes_by_expected_id)
            anchors = [anchor for anchor in self.anchors if anchor.net_name == net.name]
            segments = [segment for segment in self.segments if segment.net_name == net.name]
            by_net[net.name] = {
                "expected_pins": pins,
                "rendered_pins": rendered_pins,
                "route_style": self.route_styles.get(net.name),
                "physical_island_count": len(physical_roots.get(net.name, set())),
                "logical_island_count_after_labels": len(logical_roots.get(net.name, set())),
                "label_count": sum(1 for anchor in anchors if anchor.kind == "label_anchor"),
                "power_symbol_count": sum(1 for anchor in anchors if anchor.kind == "power_symbol_anchor"),
                "dangling_segment_count": sum(1 for segment in segments if all(count == 0 for count in segment.external_contacts.values())),
                "orphan_element_count": sum(1 for segment in segments if not segment.net_name),
                "missing_pins": [pin_id for pin_id in pins if pin_id not in self.pin_nodes_by_expected_id],
            }
        return {
            "connection_tolerance_px": self.tolerance,
            "node_count": len(self.graph.nodes),
            "edge_count": len(self.graph.edges),
            "net_count": len(by_net),
            "nets": by_net,
        }

    def _add_node(
        self,
        kind: str,
        net_name: str | None,
        component_ref: str | None = None,
        pin_name: str | None = None,
        point: tuple[float, float] | None = None,
        scene_element_id: str | None = None,
    ) -> str:
        node_id = f"rcn:{len(self.graph.nodes)}"
        self.graph.nodes.append(RenderedConnectionNode(node_id, kind, net_name, component_ref, pin_name, point, scene_element_id))
        self.uf.add(node_id)
        return node_id

    def _connect(self, node_a: str, node_b: str, kind: str, net_name: str | None, scene_element_ids: tuple[str, ...] = ()) -> None:
        self.uf.union(node_a, node_b)
        self.graph.edges.append(RenderedConnectionEdge(node_a, node_b, kind, net_name, scene_element_ids))


def validate_rendered_connectivity(circuit: Circuit, scene: SchematicScene, tolerance: float = CONNECTION_TOLERANCE_PX) -> tuple[list[Diagnostic], dict[str, Any]]:
    diagnostics, graph = _RenderedConnectivityBuilder(circuit, scene, tolerance).build()
    return diagnostics, graph.metrics


def rendered_connectivity_graph(circuit: Circuit, scene: SchematicScene, tolerance: float = CONNECTION_TOLERANCE_PX) -> tuple[RenderedConnectivityGraph, list[Diagnostic]]:
    diagnostics, graph = _RenderedConnectivityBuilder(circuit, scene, tolerance).build()
    return graph, diagnostics


def _expected_pin_maps(circuit: Circuit) -> tuple[dict[str, set[tuple[str, str]]], dict[str, list[str]]]:
    keys_by_net: dict[str, set[tuple[str, str]]] = defaultdict(set)
    ids_by_net: dict[str, list[str]] = defaultdict(list)
    for net in circuit.nets:
        for pin in net.pins:
            for key in _pinref_keys(pin):
                keys_by_net[net.name].add(key)
            ids_by_net[net.name].append(_pin_id(pin))
    return keys_by_net, ids_by_net


def _pinref_keys(pin: PinRef) -> set[tuple[str, str]]:
    values = {pin.pin_name}
    if pin.resolved_name:
        values.add(pin.resolved_name)
    if pin.resolved_number:
        values.add(pin.resolved_number)
    return {(pin.component_ref, value) for value in values if value}


def _scene_pin_keys(element: SceneElement) -> set[tuple[str, str]]:
    values = {value for value in (element.pin_name, element.pin_number) if value}
    return {(element.component_ref or "", value) for value in values}


def _pin_id(pin: PinRef) -> str:
    return f"{pin.component_ref}.{pin.resolved_name or pin.pin_name}"


def _pin_id_matches_scene(pin_id: str, element: SceneElement) -> bool:
    ref, _, pin_name = pin_id.partition(".")
    return ref == element.component_ref and pin_name in {element.pin_name, element.pin_number}


def _line_segment_from_element(element: SceneElement) -> tuple[int, int, int, int] | None:
    primitive = next((primitive for primitive in element.primitives if primitive.kind == "line"), None)
    if primitive:
        geometry = primitive.geometry
        return (round(float(geometry["x1"])), round(float(geometry["y1"])), round(float(geometry["x2"])), round(float(geometry["y2"])))
    segment = element.metadata.get("segment")
    if segment and len(segment) == 4:
        return (int(segment[0]), int(segment[1]), int(segment[2]), int(segment[3]))
    return None


def _pin_center(element: SceneElement) -> tuple[float, float]:
    primitive = element.primitives[0].geometry if element.primitives else {}
    if "cx" in primitive and "cy" in primitive:
        return (float(primitive["cx"]), float(primitive["cy"]))
    return _element_center(element)


def _element_center(element: SceneElement) -> tuple[float, float]:
    return ((element.bounds.min_x + element.bounds.max_x) / 2, (element.bounds.min_y + element.bounds.max_y) / 2)


def _point_distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return hypot(a[0] - b[0], a[1] - b[1])


def _point_on_segment(point: tuple[float, float], segment: tuple[int, int, int, int], tolerance: float) -> bool:
    x, y = point
    x1, y1, x2, y2 = segment
    if x1 == x2:
        return abs(x - x1) <= tolerance and min(y1, y2) - tolerance <= y <= max(y1, y2) + tolerance
    if y1 == y2:
        return abs(y - y1) <= tolerance and min(x1, x2) - tolerance <= x <= max(x1, x2) + tolerance
    length = _point_distance((x1, y1), (x2, y2))
    if length == 0:
        return _point_distance(point, (x1, y1)) <= tolerance
    distance = abs((y2 - y1) * x - (x2 - x1) * y + x2 * y1 - y2 * x1) / length
    return distance <= tolerance and min(x1, x2) - tolerance <= x <= max(x1, x2) + tolerance and min(y1, y2) - tolerance <= y <= max(y1, y2) + tolerance


def _nearest_segment_endpoint_node(point: tuple[float, float], segment: _SegmentInfo) -> str:
    start = (segment.segment[0], segment.segment[1])
    end = (segment.segment[2], segment.segment[3])
    return segment.start_node if _point_distance(point, start) <= _point_distance(point, end) else segment.end_node


def _mark_nearest_endpoint_contact(point: tuple[float, float], segment: _SegmentInfo) -> None:
    start = (segment.segment[0], segment.segment[1])
    end = (segment.segment[2], segment.segment[3])
    if _point_distance(point, start) <= _point_distance(point, end):
        segment.external_contacts["start"] += 1
    else:
        segment.external_contacts["end"] += 1


def _segment_endpoint_touching_box(segment: _SegmentInfo, box: tuple[float, float, float, float], tolerance: float) -> str | None:
    padded = Bounds.from_tuple(box).expanded(tolerance).as_tuple()
    start = (segment.segment[0], segment.segment[1])
    end = (segment.segment[2], segment.segment[3])
    start_inside = _point_in_box(start, padded)
    end_inside = _point_in_box(end, padded)
    if start_inside and end_inside:
        center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        return segment.start_node if _point_distance(start, center) < _point_distance(end, center) else segment.end_node
    if start_inside:
        return segment.start_node
    if end_inside:
        return segment.end_node
    return None


def _same_net_segment_contacts(first: _SegmentInfo, second: _SegmentInfo, tolerance: float) -> list[tuple[str, str]]:
    contacts: list[tuple[str, str]] = []
    endpoints = [
        ((first.segment[0], first.segment[1]), first, "start", first.start_node),
        ((first.segment[2], first.segment[3]), first, "end", first.end_node),
        ((second.segment[0], second.segment[1]), second, "start", second.start_node),
        ((second.segment[2], second.segment[3]), second, "end", second.end_node),
    ]
    first_endpoints = endpoints[:2]
    second_endpoints = endpoints[2:]
    for point_a, info_a, side_a, node_a in first_endpoints:
        for point_b, info_b, side_b, node_b in second_endpoints:
            if _point_distance(point_a, point_b) <= tolerance:
                contacts.append((node_a, node_b))
                info_a.external_contacts[side_a] += 1
                info_b.external_contacts[side_b] += 1
    for point, info, side, node in first_endpoints:
        if _point_on_segment(point, second.segment, tolerance):
            contacts.append((node, _nearest_segment_endpoint_node(point, second)))
            info.external_contacts[side] += 1
            _mark_nearest_endpoint_contact(point, second)
    for point, info, side, node in second_endpoints:
        if _point_on_segment(point, first.segment, tolerance):
            contacts.append((node, _nearest_segment_endpoint_node(point, first)))
            info.external_contacts[side] += 1
            _mark_nearest_endpoint_contact(point, first)
    return contacts


def _point_in_box(point: tuple[float, float], box: tuple[float, float, float, float]) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def _diag(severity: Severity, code: str, message: str, **kwargs: Any) -> Diagnostic:
    metadata = dict(kwargs.pop("metadata", {}) or {})
    metadata.setdefault("subsystem", "rendered_connectivity")
    return Diagnostic(severity=severity, code=code, message=message, metadata=metadata, **kwargs)


def _dedupe(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    seen: set[tuple[str | None, str | None, str | None, str | None, str]] = set()
    result: list[Diagnostic] = []
    for diagnostic in diagnostics:
        key = (diagnostic.code, diagnostic.component_ref, diagnostic.pin_ref, diagnostic.net_name, diagnostic.message)
        if key in seen:
            continue
        seen.add(key)
        result.append(diagnostic)
    return result
