from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from heapq import heappop, heappush
from typing import Any, Protocol

from .component_library import ComponentLibrary
from .geometry import absolute_pin_point, component_body_box, component_size, visual_pin_side
from .models import Circuit, ComponentDefinition, Diagnostic, Layout, NetRouteStyle, Placement, RenderQualityMode, RoutedNet
from .scene_builder import build_schematic_scene
from .visual_drc import visual_drc_from_scene

GridEdge = tuple[tuple[int, int], tuple[int, int]]
POWER_NET_NAMES = {"GND", "AGND", "DGND", "PGND", "VCC", "VDD", "VSS", "VEE", "VBAT", "3V3", "5V", "12V", "-12V", "+12V", "+5V", "+3V3", "V+", "V-"}
CONTROL_SIGNAL_TOKENS = ("SENSE", "CTRL", "CONTROL", "ENABLE", "EN", "GATE", "ADC", "FB", "TRIG", "THRESH", "RESET")


class RoutingEngine(Protocol):
    def route(self, circuit: Circuit, library: ComponentLibrary, layout: Layout) -> list[RoutedNet]:
        """Return orthogonal wire segments for the current placement."""


@dataclass
class NetRouteStyleCandidate:
    net_name: str
    style: NetRouteStyle
    routing_succeeded: bool
    visual_diagnostics: list[Diagnostic]
    new_visual_diagnostics: list[Diagnostic]
    estimated_length: float
    actual_length: float | None
    bend_count: int | None
    label_count: int
    symbol_count: int
    readability_score: float
    rejection_reasons: list[str]
    selected: bool = False

    def as_metadata(self) -> dict[str, Any]:
        return {
            "style": self.style.value,
            "routing_succeeded": self.routing_succeeded,
            "visual_diagnostic_codes": sorted(diag.code for diag in self.visual_diagnostics if diag.code),
            "new_visual_diagnostic_codes": sorted(diag.code for diag in self.new_visual_diagnostics if diag.code),
            "estimated_length": self.estimated_length,
            "actual_length": self.actual_length,
            "bend_count": self.bend_count,
            "label_count": self.label_count,
            "symbol_count": self.symbol_count,
            "readability_score": self.readability_score,
            "rejection_reasons": list(self.rejection_reasons),
            "selected": self.selected,
        }


@dataclass
class ManhattanRouter:
    grid: int = 20
    obstacle_padding: int = 24
    overlap_penalty: int = 1_000_000
    validate_auto_route_styles: bool = True
    max_auto_validated_nets: int = 2

    @classmethod
    def for_quality(cls, quality_mode: RenderQualityMode | str) -> "ManhattanRouter":
        mode = RenderQualityMode(quality_mode)
        if mode == RenderQualityMode.INTERACTIVE:
            return cls(validate_auto_route_styles=False, max_auto_validated_nets=0)
        if mode == RenderQualityMode.AUDIT:
            return cls(validate_auto_route_styles=True, max_auto_validated_nets=12)
        return cls(validate_auto_route_styles=True, max_auto_validated_nets=12)

    @classmethod
    def for_interactive(cls) -> "ManhattanRouter":
        return cls.for_quality(RenderQualityMode.INTERACTIVE)

    @classmethod
    def for_strict(cls) -> "ManhattanRouter":
        return cls.for_quality(RenderQualityMode.STRICT)

    @classmethod
    def for_audit(cls) -> "ManhattanRouter":
        return cls.for_quality(RenderQualityMode.AUDIT)

    def route(self, circuit: Circuit, library: ComponentLibrary, layout: Layout) -> list[RoutedNet]:
        if self.validate_auto_route_styles:
            initial_routes = self._route_core(circuit, library, layout)
            selected_styles, validation_records = self._validate_auto_route_styles(circuit, library, layout, initial_routes)
            if validation_records:
                return self._route_core(circuit, library, layout, auto_style_overrides=selected_styles, validation_records=validation_records)
            return initial_routes
        return self._route_core(circuit, library, layout)

    def _route_core(
        self,
        circuit: Circuit,
        library: ComponentLibrary,
        layout: Layout,
        auto_style_overrides: dict[str, NetRouteStyle] | None = None,
        validation_records: dict[str, dict[str, Any]] | None = None,
    ) -> list[RoutedNet]:
        routes: list[RoutedNet] = []
        ref_to_id = {component.ref: component.component_id for component in circuit.components}
        obstacles = self._obstacles(circuit, library, layout)
        body_obstacles_by_ref = self._body_obstacles_by_ref(circuit, library, layout)
        body_obstacles = list(body_obstacles_by_ref.values())
        bounds = self._bounds(layout)
        used_edges: set[GridEdge] = set()
        for net in sorted(circuit.nets, key=lambda item: (len(item.pins), item.name)):
            pin_points: list[tuple[tuple[int, int], str]] = []
            endpoint_refs: list[str] = []
            routed = RoutedNet(name=net.name)
            for pinref in net.pins:
                definition = library.get(ref_to_id.get(pinref.component_ref, ""))
                placement = layout.components.get(pinref.component_ref)
                pin = definition.resolve_pin(pinref.pin_name) if definition else None
                if not definition or not placement or not pin:
                    routed.warnings.append(f"Cannot route {pinref.component_ref}.{pinref.pin_name}")
                    continue
                pin_point = absolute_pin_point(definition, placement, pin)
                side = visual_pin_side(definition, pin, placement)
                pin_points.append((pin_point, side))
                endpoint_refs.append(pinref.component_ref)
                routed.endpoints.append(
                    {
                        "component_ref": pinref.component_ref,
                        "pin_name": pin.name,
                        "pin_number": pin.number,
                        "side": side,
                        "x": pin_point[0],
                        "y": pin_point[1],
                    }
                )
            decision = self._route_style_decision(
                net.name,
                pin_points,
                endpoint_refs,
                layout,
                body_obstacles_by_ref,
                auto_style_overrides or {},
                validation_records or {},
            )
            render_style = str(decision["render_style"])
            routed.render_style = render_style
            routed.metadata["route_style"] = {key: value for key, value in decision.items() if key != "render_style"}
            if warning := decision.get("route_style_warning"):
                routed.warnings.append(str(warning))
            if len(pin_points) >= 2:
                if render_style == "power_symbol":
                    # Power connectivity is represented with local symbols at each endpoint.
                    routes.append(routed)
                    continue
                if render_style == "net_label":
                    # Distant named nets stay electrically common through matching labels.
                    routes.append(routed)
                    continue
                if len(pin_points) == 2:
                    direct = (pin_points[0][0][0], pin_points[0][0][1], pin_points[1][0][0], pin_points[1][0][1])
                    if (direct[0] == direct[2] or direct[1] == direct[3]) and not self._segment_crosses_obstacle(direct, body_obstacles):
                        routed.segments.append(direct)
                        routed.segments = self._simplify_segments(routed.segments, {point for point, _ in pin_points})
                        routes.append(routed)
                        continue
                if net.name in POWER_NET_NAMES:
                    self._route_rail_net(net.name, pin_points, routed, used_edges, obstacles, body_obstacles, bounds)
                    routes.append(routed)
                    continue
                route_edges: set[GridEdge] = set()
                escaped_points = [(pin_point, self._choose_escape_point(pin_point, side, used_edges)) for pin_point, side in pin_points]
                route_points = [external for _, external in escaped_points]
                root = self._rail_point(net.name, route_points) if net.name in POWER_NET_NAMES else route_points[0]
                root = self._nearest_free(root, obstacles, bounds)
                for pin_point, external in escaped_points:
                    escape_segment = (pin_point[0], pin_point[1], external[0], external[1])
                    escape_segments = self._detour_overlaps([escape_segment], used_edges - route_edges, body_obstacles)
                    routed.segments.extend(escape_segments)
                    for segment in escape_segments:
                        self._reserve_segment(segment, used_edges)
                        self._reserve_segment(segment, route_edges)
                    if external == root:
                        continue
                    path = self._astar(root, external, obstacles, bounds, used_edges - route_edges)
                    if path:
                        path_segments = self._path_to_segments(path)
                        path_segments = self._connect_exact_route_endpoints(root, external, path, path_segments)
                        path_segments = self._detour_overlaps(path_segments, used_edges - route_edges, body_obstacles)
                        routed.segments.extend(path_segments)
                        for segment in path_segments:
                            self._reserve_segment(segment, used_edges)
                            self._reserve_segment(segment, route_edges)
                    else:
                        routed.segments.extend(self._unrouted_fallback(root, external))
                        routed.warnings.append(f"Could not find obstacle-free route for {net.name}")
            elif pin_points:
                routed.warnings.append(f"Net {net.name} has only one routable pin")
            routed.segments = self._simplify_segments(routed.segments, {point for point, _ in pin_points})
            routed.junctions = self._junctions_from_segments(routed.segments)
            routes.append(routed)
        return routes

    def _route_style_decision(
        self,
        net_name: str,
        pin_points: list[tuple[tuple[int, int], str]],
        endpoint_refs: list[str],
        layout: Layout,
        body_obstacles_by_ref: dict[str, tuple[int, int, int, int]],
        auto_style_overrides: dict[str, NetRouteStyle],
        validation_records: dict[str, dict[str, Any]],
    ) -> dict[str, object]:
        manual = self._manual_route_style(net_name, layout)
        endpoint_count = len(pin_points)
        estimated_length = self._estimated_direct_length([point for point, _ in pin_points])
        estimated_bends = self._estimated_direct_bends([point for point, _ in pin_points])
        direct_status = self._direct_candidate_status(pin_points, endpoint_refs, body_obstacles_by_ref)
        if manual and manual != NetRouteStyle.AUTO:
            return {
                "selected_style": manual.value,
                "manual_override": manual.value,
                "reason": "manual layout override",
                "endpoint_count": endpoint_count,
                "estimated_direct_length": estimated_length,
                "estimated_direct_bends": estimated_bends,
                "direct_drc_status": direct_status,
                "label_drc_status": "manual_override",
                "render_style": self._render_style_for_route_style(manual),
            }
        if net_name in auto_style_overrides:
            selected = auto_style_overrides[net_name]
            record = dict(validation_records.get(net_name, {}))
            record.update(
                {
                    "selected_style": selected.value,
                    "manual_override": None,
                    "endpoint_count": endpoint_count,
                    "fanout_count": endpoint_count,
                    "estimated_direct_length": estimated_length,
                    "estimated_direct_bends": estimated_bends,
                    "direct_drc_status": self._candidate_status_from_record(record, NetRouteStyle.DIRECT, direct_status),
                    "label_drc_status": self._candidate_status_from_record(record, NetRouteStyle.LABEL, "not_considered"),
                    "render_style": self._render_style_for_route_style(selected),
                }
            )
            return record
        auto = self._auto_route_style(net_name, pin_points, direct_status)
        return {
            "selected_style": auto.value,
            "manual_override": None,
            "reason": self._auto_route_style_reason(net_name, pin_points, auto),
            "endpoint_count": endpoint_count,
            "fanout_count": endpoint_count,
            "estimated_direct_length": estimated_length,
            "estimated_direct_bends": estimated_bends,
            "direct_drc_status": direct_status,
            "label_drc_status": "available" if auto != NetRouteStyle.DIRECT else "not_required",
            "render_style": self._render_style_for_route_style(auto),
        }

    def _manual_route_style(self, net_name: str, layout: Layout) -> NetRouteStyle | None:
        data = layout.nets.get(net_name, {})
        route_style = data.get("route_style")
        if route_style in {style.value for style in NetRouteStyle}:
            return NetRouteStyle(route_style)
        render_style = data.get("render_style")
        if render_style == "local_wire":
            return NetRouteStyle.DIRECT
        if render_style == "net_label":
            return NetRouteStyle.LABEL
        if render_style == "power_symbol":
            return NetRouteStyle.POWER_SYMBOL
        return None

    def _auto_route_style(self, net_name: str, pin_points: list[tuple[tuple[int, int], str]], direct_status: str = "clean_estimate") -> NetRouteStyle:
        endpoint_count = len(pin_points)
        points = [point for point, _ in pin_points]
        if net_name in POWER_NET_NAMES:
            return NetRouteStyle.POWER_SYMBOL
        if endpoint_count <= 1:
            return NetRouteStyle.DIRECT
        direct_is_clear = direct_status == "clean_estimate"
        span_x = max(point[0] for point in points) - min(point[0] for point in points)
        span_y = max(point[1] for point in points) - min(point[1] for point in points)
        estimated_length = self._estimated_direct_length(points)
        estimated_bends = self._estimated_direct_bends(points)
        if endpoint_count == 2:
            return NetRouteStyle.DIRECT if direct_is_clear and estimated_length <= 900 and estimated_bends <= 2 else NetRouteStyle.LABEL
        if endpoint_count == 3 and direct_is_clear and span_x <= 700 and span_y <= 520 and estimated_bends <= 4:
            return NetRouteStyle.DIRECT
        if self._is_control_or_sense_net(net_name):
            return NetRouteStyle.LABEL
        if endpoint_count > 3:
            return NetRouteStyle.LABEL
        return NetRouteStyle.DIRECT if direct_is_clear and estimated_length <= 900 else NetRouteStyle.LABEL

    def _direct_candidate_status(
        self,
        pin_points: list[tuple[tuple[int, int], str]],
        endpoint_refs: list[str],
        body_obstacles_by_ref: dict[str, tuple[int, int, int, int]],
    ) -> str:
        points = [point for point, _ in pin_points]
        if len(points) <= 1:
            return "clean_estimate"
        ignored_refs = set(endpoint_refs)
        body_obstacles = [box for ref, box in body_obstacles_by_ref.items() if ref not in ignored_refs]
        root = points[0]
        for point in points[1:]:
            if any(self._segment_crosses_obstacle(segment, body_obstacles) for segment in self._estimated_direct_segments(root, point)):
                return "blocked_by_component_estimate"
        return "clean_estimate"

    def _estimated_direct_segments(self, root: tuple[int, int], point: tuple[int, int]) -> list[tuple[int, int, int, int]]:
        if root[0] == point[0] or root[1] == point[1]:
            return [(root[0], root[1], point[0], point[1])]
        horizontal_first = [(root[0], root[1], point[0], root[1]), (point[0], root[1], point[0], point[1])]
        vertical_first = [(root[0], root[1], root[0], point[1]), (root[0], point[1], point[0], point[1])]
        return min((horizontal_first, vertical_first), key=lambda segments: sum(abs(x1 - x2) + abs(y1 - y2) for x1, y1, x2, y2 in segments))

    def _auto_route_style_reason(self, net_name: str, pin_points: list[tuple[tuple[int, int], str]], style: NetRouteStyle) -> str:
        endpoint_count = len(pin_points)
        if net_name in POWER_NET_NAMES:
            return "global power or ground net"
        if endpoint_count == 2 and style == NetRouteStyle.DIRECT:
            return "simple two-endpoint net"
        if endpoint_count == 3 and style == NetRouteStyle.DIRECT:
            return "local three-endpoint net"
        if self._is_control_or_sense_net(net_name):
            return "generic control or sense net prefers labels"
        if endpoint_count > 3:
            return "fanout net prefers labels"
        return "readability heuristic"

    def _is_control_or_sense_net(self, net_name: str) -> bool:
        tokens = [token for token in net_name.upper().replace("-", "_").split("_") if token]
        return any(token in CONTROL_SIGNAL_TOKENS or token.endswith("SENSE") for token in tokens)

    def _candidate_status_from_record(self, record: dict[str, Any], style: NetRouteStyle, default: str) -> str:
        by_candidate = record.get("diagnostics_by_candidate", {})
        candidate = by_candidate.get(style.value) if isinstance(by_candidate, dict) else None
        if not isinstance(candidate, dict):
            return default
        if not candidate.get("routing_succeeded", True):
            return "routing_failed"
        if candidate.get("new_visual_diagnostic_codes") or candidate.get("visual_diagnostic_codes"):
            return "scene_drc_failed"
        return "scene_clean"

    def _render_style_for_route_style(self, route_style: NetRouteStyle) -> str:
        if route_style == NetRouteStyle.POWER_SYMBOL:
            return "power_symbol"
        if route_style == NetRouteStyle.LABEL:
            return "net_label"
        return "local_wire"

    def _validate_auto_route_styles(
        self,
        circuit: Circuit,
        library: ComponentLibrary,
        layout: Layout,
        baseline_routes: list[RoutedNet],
    ) -> tuple[dict[str, NetRouteStyle], dict[str, dict[str, Any]]]:
        baseline_scene = build_schematic_scene(circuit, library, layout, baseline_routes)
        baseline_diagnostics, _ = visual_drc_from_scene(baseline_scene)
        baseline_counts = Counter(diag.code or "" for diag in baseline_diagnostics)
        routes_by_name = {route.name: route for route in baseline_routes}
        selected_styles: dict[str, NetRouteStyle] = {}
        records: dict[str, dict[str, Any]] = {}

        for net_name in self._candidate_validation_net_names(circuit, routes_by_name):
            route = routes_by_name[net_name]
            current_meta = route.metadata.get("route_style", {})
            endpoint_count = int(current_meta.get("endpoint_count", len(route.endpoints)))
            candidates = [
                self._evaluate_route_style_candidate(
                    circuit,
                    library,
                    layout,
                    net_name,
                    style,
                    baseline_counts,
                    route,
                )
                for style in self._candidate_styles_for_net(net_name, endpoint_count)
            ]
            selected = self._select_route_style_candidate(candidates)
            selected.selected = True
            selected_styles[net_name] = selected.style
            records[net_name] = self._route_style_validation_record(net_name, route, candidates, selected)
        return selected_styles, records

    def _candidate_validation_net_names(self, circuit: Circuit, routes_by_name: dict[str, RoutedNet]) -> list[str]:
        scored: list[tuple[int, str]] = []
        for net in circuit.nets:
            route = routes_by_name.get(net.name)
            if not route:
                continue
            meta = route.metadata.get("route_style", {})
            if meta.get("manual_override") is not None:
                continue
            endpoint_count = int(meta.get("endpoint_count", len(route.endpoints)))
            if endpoint_count <= 1:
                continue
            selected = str(meta.get("selected_style", ""))
            direct_status = str(meta.get("direct_drc_status", ""))
            estimated_length = int(meta.get("estimated_direct_length", 0))
            priority = 40
            if net.name in POWER_NET_NAMES:
                priority = 0
            elif direct_status != "clean_estimate":
                priority = 5
            elif selected != NetRouteStyle.DIRECT.value:
                priority = 10
            elif endpoint_count != 2:
                priority = 15
            elif estimated_length > 360:
                priority = 20
            elif len(circuit.nets) <= self.max_auto_validated_nets:
                priority = 25
            else:
                continue
            scored.append((priority, net.name))
        scored.sort(key=lambda item: (item[0], item[1]))
        return [net_name for _, net_name in scored[: self.max_auto_validated_nets]]

    def _candidate_styles_for_net(self, net_name: str, endpoint_count: int) -> list[NetRouteStyle]:
        if net_name in POWER_NET_NAMES:
            return [NetRouteStyle.POWER_SYMBOL, NetRouteStyle.DIRECT, NetRouteStyle.LABEL]
        if endpoint_count > 1:
            return [NetRouteStyle.DIRECT, NetRouteStyle.LABEL]
        return [NetRouteStyle.DIRECT]

    def _evaluate_route_style_candidate(
        self,
        circuit: Circuit,
        library: ComponentLibrary,
        layout: Layout,
        net_name: str,
        style: NetRouteStyle,
        baseline_counts: Counter[str],
        baseline_route: RoutedNet,
    ) -> NetRouteStyleCandidate:
        candidate_routes = self._route_core(circuit, library, layout, auto_style_overrides={net_name: style})
        candidate_route = next((route for route in candidate_routes if route.name == net_name), RoutedNet(name=net_name))
        scene = build_schematic_scene(circuit, library, layout, candidate_routes)
        visual_diagnostics, _ = visual_drc_from_scene(scene)
        new_visual_diagnostics = self._diagnostic_delta(visual_diagnostics, baseline_counts)
        routing_succeeded = not candidate_route.warnings
        actual_length = self._route_length(candidate_route)
        bend_count = self._route_bend_count(candidate_route)
        label_count = sum(1 for element in scene.elements if element.net_name == net_name and element.kind == "net_label")
        symbol_count = sum(1 for element in scene.elements if element.net_name == net_name and element.kind in {"power_symbol", "ground_symbol"})
        estimated_length = float(baseline_route.metadata.get("route_style", {}).get("estimated_direct_length", actual_length))
        rejection_reasons = []
        if not routing_succeeded:
            rejection_reasons.append("ROUTING_FAILED")
        rejection_reasons.extend(sorted({diag.code or "VISUAL_DRC" for diag in new_visual_diagnostics}))
        readability_score = self._route_style_readability_score(style, candidate_route, visual_diagnostics, new_visual_diagnostics, label_count, symbol_count)
        return NetRouteStyleCandidate(
            net_name=net_name,
            style=style,
            routing_succeeded=routing_succeeded,
            visual_diagnostics=visual_diagnostics,
            new_visual_diagnostics=new_visual_diagnostics,
            estimated_length=estimated_length,
            actual_length=float(actual_length),
            bend_count=bend_count,
            label_count=label_count,
            symbol_count=symbol_count,
            readability_score=readability_score,
            rejection_reasons=rejection_reasons,
        )

    def _select_route_style_candidate(self, candidates: list[NetRouteStyleCandidate]) -> NetRouteStyleCandidate:
        return min(
            candidates,
            key=lambda candidate: (
                len(candidate.new_visual_diagnostics),
                0 if candidate.routing_succeeded else 1,
                len(candidate.visual_diagnostics),
                candidate.readability_score,
                self._route_style_tie_breaker(candidate.style),
            ),
        )

    def _route_style_validation_record(
        self,
        net_name: str,
        baseline_route: RoutedNet,
        candidates: list[NetRouteStyleCandidate],
        selected: NetRouteStyleCandidate,
    ) -> dict[str, Any]:
        diagnostics_by_candidate = {candidate.style.value: candidate.as_metadata() for candidate in candidates}
        candidate_styles = [candidate.style.value for candidate in candidates]
        has_clean_candidate = any(not candidate.new_visual_diagnostics and candidate.routing_succeeded for candidate in candidates)
        reason = "scene DRC candidate validation selected cleanest readable route"
        warning = None
        if selected.new_visual_diagnostics or not selected.routing_succeeded:
            reason = "all route-style candidates had issues; selected least-bad candidate"
            warning = f"Route-style AUTO for {net_name} selected {selected.style.value} with candidate issues"
        return {
            "reason": reason,
            "candidate_validation": "scene_drc",
            "validation_scope": "bounded_auto_net",
            "candidates_considered": candidate_styles,
            "diagnostics_by_candidate": diagnostics_by_candidate,
            "actual_or_estimated_lengths": {candidate.style.value: candidate.actual_length or candidate.estimated_length for candidate in candidates},
            "bend_counts": {candidate.style.value: candidate.bend_count for candidate in candidates},
            "label_counts": {candidate.style.value: candidate.label_count for candidate in candidates},
            "symbol_counts": {candidate.style.value: candidate.symbol_count for candidate in candidates},
            "has_clean_candidate": has_clean_candidate,
            "route_style_warning": warning,
            "selected_candidate": selected.as_metadata(),
            "initial_heuristic_style": baseline_route.metadata.get("route_style", {}).get("selected_style"),
        }

    def _diagnostic_delta(self, diagnostics: list[Diagnostic], baseline_counts: Counter[str]) -> list[Diagnostic]:
        used: Counter[str] = Counter()
        delta: list[Diagnostic] = []
        for diag in diagnostics:
            code = diag.code or ""
            if code and used[code] < baseline_counts.get(code, 0):
                used[code] += 1
            else:
                delta.append(diag)
        return delta

    def _route_style_readability_score(
        self,
        style: NetRouteStyle,
        route: RoutedNet,
        visual_diagnostics: list[Diagnostic],
        new_visual_diagnostics: list[Diagnostic],
        label_count: int,
        symbol_count: int,
    ) -> float:
        length = self._route_length(route)
        bends = self._route_bend_count(route)
        score = len(new_visual_diagnostics) * 1_000_000 + len(visual_diagnostics) * 25_000
        score += length * 0.5 + bends * 120 + label_count * 80 + symbol_count * 50
        if style == NetRouteStyle.POWER_SYMBOL and route.name in POWER_NET_NAMES:
            score -= 120
        if style == NetRouteStyle.DIRECT and len(route.endpoints) == 2 and length <= 420:
            score -= 80
        if style == NetRouteStyle.LABEL and (len(route.endpoints) > 2 or length > 700):
            score -= 60
        return score

    def _route_style_tie_breaker(self, style: NetRouteStyle) -> int:
        return {
            NetRouteStyle.POWER_SYMBOL: 0,
            NetRouteStyle.DIRECT: 1,
            NetRouteStyle.LABEL: 2,
            NetRouteStyle.AUTO: 3,
        }[style]

    def _route_length(self, route: RoutedNet) -> int:
        return sum(abs(x1 - x2) + abs(y1 - y2) for x1, y1, x2, y2 in route.segments)

    def _route_bend_count(self, route: RoutedNet) -> int:
        bends = 0
        previous_orientation: str | None = None
        previous_end: tuple[int, int] | None = None
        for segment in route.segments:
            x1, y1, x2, y2 = segment
            orientation = "vertical" if x1 == x2 else "horizontal" if y1 == y2 else "diagonal"
            start = (x1, y1)
            end = (x2, y2)
            if previous_orientation and previous_end == start and previous_orientation != orientation:
                bends += 1
            previous_orientation = orientation
            previous_end = end
        return bends

    def _estimated_direct_length(self, points: list[tuple[int, int]]) -> int:
        if len(points) < 2:
            return 0
        root = points[0]
        return sum(abs(root[0] - point[0]) + abs(root[1] - point[1]) for point in points[1:])

    def _estimated_direct_bends(self, points: list[tuple[int, int]]) -> int:
        if len(points) < 2:
            return 0
        return sum(0 if points[0][0] == point[0] or points[0][1] == point[1] else 1 for point in points[1:])

    def _route_rail_net(
        self,
        name: str,
        pin_points: list[tuple[tuple[int, int], str]],
        routed: RoutedNet,
        used_edges: set[GridEdge],
        obstacles: list[tuple[int, int, int, int]],
        body_obstacles: list[tuple[int, int, int, int]],
        bounds: tuple[int, int, int, int],
    ) -> None:
        escaped = [(pin_point, self._choose_escape_point(pin_point, side, used_edges)) for pin_point, side in pin_points]
        xs = [point[0] for _, point in escaped]
        pin_ys = [pin_point[1] for pin_point, _ in pin_points]
        bus_y = self._snap(max(pin_ys) + self.grid * 4) if name == "GND" else self._snap(min(pin_ys) - self.grid * 4)
        min_x = self._snap_down(min(xs))
        max_x = self._snap_up(max(xs))
        bus = (min_x, bus_y, max_x, bus_y)
        routed.segments.append(bus)
        self._reserve_segment(bus, used_edges)
        for pin_point, external in escaped:
            for segment in self._pin_escape_segments(pin_point, external):
                routed.segments.append(segment)
                self._reserve_segment(segment, used_edges)
            tap = (external[0], bus_y)
            direct_drop = (external[0], external[1], tap[0], tap[1])
            if not self._segment_crosses_obstacle(direct_drop, body_obstacles):
                path_segments = [direct_drop]
            else:
                grid_external = self._snap_point(external)
                snapped_tap = (grid_external[0], bus_y)
                path = self._astar(grid_external, snapped_tap, obstacles, bounds, used_edges)
                path_segments = self._path_to_segments(path) if path else [(grid_external[0], grid_external[1], snapped_tap[0], snapped_tap[1])]
            routed.segments.extend(path_segments)
            for segment in path_segments:
                self._reserve_segment(segment, used_edges)
        routed.labels.append((min_x + 8, bus_y - 8, name))
        routed.segments = self._simplify_segments(routed.segments, {pin_point for pin_point, _ in pin_points})
        routed.junctions = self._junctions_from_segments(routed.segments)

    def _pin_point(self, x: int, y: int, width: int, height: int, side: str, position: int, pitch: int) -> tuple[int, int]:
        offset = position * pitch
        if side == "left":
            return (x, y + offset)
        if side == "right":
            return (x + width, y + offset)
        if side == "top":
            return (x + offset, y)
        return (x + offset, y + height)

    def _rail_point(self, name: str, points: list[tuple[int, int]]) -> tuple[int, int]:
        xs = [point[0] for point in points]
        y = min(point[1] for point in points) - 50 if name != "GND" else max(point[1] for point in points) + 70
        return (round(sum(xs) / len(xs) / self.grid) * self.grid, round(y / self.grid) * self.grid)

    def _escape_point(self, pin_point: tuple[int, int], side: str) -> tuple[int, int]:
        x, y = pin_point
        offset = self.grid * 4
        if side == "left":
            return (self._snap(x - offset), y)
        if side == "right":
            return (self._snap(x + offset), y)
        if side == "top":
            return (x, self._snap(y - offset))
        return (x, self._snap(y + offset))

    def _choose_escape_point(
        self,
        pin_point: tuple[int, int],
        side: str,
        used_edges: set[GridEdge],
    ) -> tuple[int, int]:
        for distance in range(4, 13):
            candidate = self._escape_point_at(pin_point, side, self.grid * distance)
            segment = (pin_point[0], pin_point[1], candidate[0], candidate[1])
            if not any(edge in used_edges for edge in self._segment_edges(segment)):
                return candidate
        return self._escape_point(pin_point, side)

    def _escape_point_at(self, pin_point: tuple[int, int], side: str, offset: int) -> tuple[int, int]:
        x, y = pin_point
        if side == "left":
            return (self._snap(x - offset), y)
        if side == "right":
            return (self._snap(x + offset), y)
        if side == "top":
            return (x, self._snap(y - offset))
        return (x, self._snap(y + offset))

    def _pin_escape_segments(
        self,
        pin_point: tuple[int, int],
        external: tuple[int, int],
    ) -> list[tuple[int, int, int, int]]:
        if pin_point[0] == external[0] or pin_point[1] == external[1]:
            return [(pin_point[0], pin_point[1], external[0], external[1])]
        bend = (external[0], pin_point[1])
        return [
            (pin_point[0], pin_point[1], bend[0], bend[1]),
            (bend[0], bend[1], external[0], external[1]),
        ]

    def _obstacles(self, circuit: Circuit, library: ComponentLibrary, layout: Layout) -> list[tuple[int, int, int, int]]:
        boxes: list[tuple[int, int, int, int]] = []
        for instance in circuit.components:
            definition = library.get(instance.component_id)
            placement = layout.components.get(instance.ref)
            if not definition or not placement:
                continue
            boxes.append(self._obstacle_box(definition, placement))
        return boxes

    def _obstacle_box(self, definition: ComponentDefinition, placement: Placement) -> tuple[int, int, int, int]:
        width, height = component_size(definition, placement)
        return (
            self._snap_down(placement.x - self.obstacle_padding),
            self._snap_down(placement.y - self.obstacle_padding),
            self._snap_up(placement.x + width + self.obstacle_padding),
            self._snap_up(placement.y + height + self.obstacle_padding),
        )

    def _body_obstacles(self, circuit: Circuit, library: ComponentLibrary, layout: Layout) -> list[tuple[int, int, int, int]]:
        return list(self._body_obstacles_by_ref(circuit, library, layout).values())

    def _body_obstacles_by_ref(self, circuit: Circuit, library: ComponentLibrary, layout: Layout) -> dict[str, tuple[int, int, int, int]]:
        boxes: dict[str, tuple[int, int, int, int]] = {}
        for instance in circuit.components:
            definition = library.get(instance.component_id)
            placement = layout.components.get(instance.ref)
            if not definition or not placement:
                continue
            boxes[instance.ref] = tuple(int(value) for value in component_body_box(definition, placement))
        return boxes

    def _bounds(self, layout: Layout) -> tuple[int, int, int, int]:
        width = int(layout.canvas.get("width", 1300))
        height = int(layout.canvas.get("height", 900))
        return (-self.grid * 4, -self.grid * 4, self._snap_up(width + self.grid * 4), self._snap_up(height + self.grid * 4))

    def _astar(
        self,
        start: tuple[int, int],
        goal: tuple[int, int],
        obstacles: list[tuple[int, int, int, int]],
        bounds: tuple[int, int, int, int],
        used_edges: set[GridEdge],
    ) -> list[tuple[int, int]] | None:
        start = self._nearest_free(self._snap_point(start), obstacles, bounds)
        goal = self._nearest_free(self._snap_point(goal), obstacles, bounds)
        frontier: list[tuple[int, int, tuple[int, int]]] = []
        heappush(frontier, (0, 0, start))
        came_from: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
        cost_so_far: dict[tuple[int, int], int] = {start: 0}
        order = 0
        while frontier:
            _, _, current = heappop(frontier)
            if current == goal:
                return self._reconstruct(came_from, current)
            for next_point in self._neighbors(current, obstacles, bounds):
                new_cost = cost_so_far[current] + self.grid + self._edge_penalty(current, next_point, used_edges)
                if next_point not in cost_so_far or new_cost < cost_so_far[next_point]:
                    cost_so_far[next_point] = new_cost
                    order += 1
                    priority = new_cost + self._manhattan(next_point, goal)
                    heappush(frontier, (priority, order, next_point))
                    came_from[next_point] = current
        return None

    def _neighbors(
        self,
        point: tuple[int, int],
        obstacles: list[tuple[int, int, int, int]],
        bounds: tuple[int, int, int, int],
    ) -> list[tuple[int, int]]:
        x, y = point
        candidates = [(x + self.grid, y), (x - self.grid, y), (x, y + self.grid), (x, y - self.grid)]
        return [
            candidate
            for candidate in candidates
            if self._in_bounds(candidate, bounds)
            and not self._blocked(candidate, obstacles)
        ]

    def _nearest_free(
        self,
        point: tuple[int, int],
        obstacles: list[tuple[int, int, int, int]],
        bounds: tuple[int, int, int, int],
    ) -> tuple[int, int]:
        point = self._snap_point(point)
        if self._in_bounds(point, bounds) and not self._blocked(point, obstacles):
            return point
        for radius in range(self.grid, self.grid * 20, self.grid):
            candidates: list[tuple[int, int]] = []
            for dx in range(-radius, radius + self.grid, self.grid):
                candidates.append((point[0] + dx, point[1] - radius))
                candidates.append((point[0] + dx, point[1] + radius))
            for dy in range(-radius + self.grid, radius, self.grid):
                candidates.append((point[0] - radius, point[1] + dy))
                candidates.append((point[0] + radius, point[1] + dy))
            for candidate in sorted(candidates, key=lambda item: self._manhattan(item, point)):
                if self._in_bounds(candidate, bounds) and not self._blocked(candidate, obstacles):
                    return candidate
        return point

    def _path_to_segments(self, path: list[tuple[int, int]]) -> list[tuple[int, int, int, int]]:
        if len(path) < 2:
            return []
        compressed: list[tuple[int, int]] = [path[0]]
        last_direction: tuple[int, int] | None = None
        for previous, current in zip(path, path[1:]):
            direction = (current[0] - previous[0], current[1] - previous[1])
            if last_direction is not None and direction != last_direction:
                compressed.append(previous)
            last_direction = direction
        compressed.append(path[-1])
        return [(a[0], a[1], b[0], b[1]) for a, b in zip(compressed, compressed[1:]) if a != b]

    def _connect_exact_route_endpoints(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        path: list[tuple[int, int]],
        segments: list[tuple[int, int, int, int]],
    ) -> list[tuple[int, int, int, int]]:
        segments = [*segments]
        if segments and start != path[0] and self._point_on_segment(start, segments[0]):
            _, _, x2, y2 = segments[0]
            segments[0] = (start[0], start[1], x2, y2)
        if segments and end != path[-1] and self._point_on_segment(end, segments[-1]):
            x1, y1, _, _ = segments[-1]
            segments[-1] = (x1, y1, end[0], end[1])
        connected: list[tuple[int, int, int, int]] = []
        if path and start != path[0] and not (segments and self._point_on_segment(start, segments[0])):
            connected.extend(self._pin_escape_segments(start, path[0]))
        connected.extend(segments)
        if path and end != path[-1] and not (segments and self._point_on_segment(end, segments[-1])):
            connected.extend(self._pin_escape_segments(path[-1], end))
        return connected

    def _point_on_segment(self, point: tuple[int, int], segment: tuple[int, int, int, int]) -> bool:
        px, py = point
        x1, y1, x2, y2 = segment
        if x1 == x2 == px:
            return min(y1, y2) <= py <= max(y1, y2)
        if y1 == y2 == py:
            return min(x1, x2) <= px <= max(x1, x2)
        return False

    def _junctions_from_segments(self, segments: list[tuple[int, int, int, int]]) -> list[tuple[int, int]]:
        candidates = {(segment[0], segment[1]) for segment in segments} | {(segment[2], segment[3]) for segment in segments}
        candidates.update(self._segment_intersection_points(segments))
        junctions: list[tuple[int, int]] = []
        for point in candidates:
            branches = 0
            for segment in segments:
                if not self._point_on_segment(point, segment):
                    continue
                endpoints = {(segment[0], segment[1]), (segment[2], segment[3])}
                branches += 1 if point in endpoints else 2
            if branches >= 3:
                junctions.append(point)
        return sorted(junctions)

    def _simplify_segments(
        self,
        segments: list[tuple[int, int, int, int]],
        preserved_points: set[tuple[int, int]] | None = None,
    ) -> list[tuple[int, int, int, int]]:
        simplified: list[tuple[int, int, int, int]] = []
        protected_points = preserved_points or set()
        for index, segment in enumerate(segments):
            if segment[0] == segment[2] and segment[1] == segment[3]:
                continue
            if any(index != other_index and self._segment_contains(other, segment) for other_index, other in enumerate(segments)):
                continue
            simplified.append(segment)
        return self._trim_dangling_tails(self._merge_collinear_segments(simplified), protected_points)

    def _merge_collinear_segments(self, segments: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
        changed = True
        merged = [*segments]
        while changed:
            changed = False
            next_segments: list[tuple[int, int, int, int]] = []
            consumed: set[int] = set()
            for index, segment in enumerate(merged):
                if index in consumed:
                    continue
                replacement = segment
                for other_index in range(index + 1, len(merged)):
                    if other_index in consumed:
                        continue
                    combined = self._combine_collinear(replacement, merged[other_index])
                    if combined:
                        replacement = combined
                        consumed.add(other_index)
                        changed = True
                next_segments.append(replacement)
            merged = next_segments
        return merged

    def _combine_collinear(
        self,
        a: tuple[int, int, int, int],
        b: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int] | None:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        if ax1 == ax2 == bx1 == bx2:
            a_min, a_max = sorted((ay1, ay2))
            b_min, b_max = sorted((by1, by2))
            if max(a_min, b_min) <= min(a_max, b_max):
                return (ax1, min(a_min, b_min), ax1, max(a_max, b_max))
        if ay1 == ay2 == by1 == by2:
            a_min, a_max = sorted((ax1, ax2))
            b_min, b_max = sorted((bx1, bx2))
            if max(a_min, b_min) <= min(a_max, b_max):
                return (min(a_min, b_min), ay1, max(a_max, b_max), ay1)
        return None

    def _trim_dangling_tails(
        self,
        segments: list[tuple[int, int, int, int]],
        preserved_points: set[tuple[int, int]] | None = None,
    ) -> list[tuple[int, int, int, int]]:
        endpoints = [point for segment in segments for point in ((segment[0], segment[1]), (segment[2], segment[3]))]
        endpoint_counts = Counter(endpoints)
        protected_points = preserved_points or set()
        trimmed: list[tuple[int, int, int, int]] = []
        connection_points_by_segment = {segment: self._connection_points_on_segment(segment, segments) for segment in segments}
        for segment in segments:
            p1 = (segment[0], segment[1])
            p2 = (segment[2], segment[3])
            internal_points = sorted(
                {
                    point
                    for point in connection_points_by_segment.get(segment, set())
                    if point not in {p1, p2} and self._point_on_segment(point, segment)
                },
                key=lambda point: self._manhattan(point, p1),
            )
            if p1 not in protected_points and not self._point_connected_to_other_segment(p1, segment, segments, endpoint_counts) and internal_points:
                p1 = internal_points[0]
            internal_points = sorted(
                {
                    point
                    for point in connection_points_by_segment.get(segment, set())
                    if point not in {p1, p2} and self._point_on_segment(point, (p1[0], p1[1], p2[0], p2[1]))
                },
                key=lambda point: self._manhattan(point, p2),
            )
            if p2 not in protected_points and not self._point_connected_to_other_segment(p2, segment, segments, endpoint_counts) and internal_points:
                p2 = internal_points[0]
            if p1 != p2:
                trimmed.append((p1[0], p1[1], p2[0], p2[1]))
        return trimmed

    def _point_connected_to_other_segment(
        self,
        point: tuple[int, int],
        segment: tuple[int, int, int, int],
        segments: list[tuple[int, int, int, int]],
        endpoint_counts: Counter[tuple[int, int]],
    ) -> bool:
        if endpoint_counts[point] > 1:
            return True
        return any(other != segment and self._point_on_segment(point, other) for other in segments)

    def _segment_contains(self, outer: tuple[int, int, int, int], inner: tuple[int, int, int, int]) -> bool:
        ox1, oy1, ox2, oy2 = outer
        ix1, iy1, ix2, iy2 = inner
        if ox1 == ox2 == ix1 == ix2:
            return min(oy1, oy2) <= min(iy1, iy2) and max(iy1, iy2) <= max(oy1, oy2) and outer != inner
        if oy1 == oy2 == iy1 == iy2:
            return min(ox1, ox2) <= min(ix1, ix2) and max(ix1, ix2) <= max(ox1, ox2) and outer != inner
        return False

    def _connection_points_on_segment(
        self,
        segment: tuple[int, int, int, int],
        segments: list[tuple[int, int, int, int]],
    ) -> set[tuple[int, int]]:
        points: set[tuple[int, int]] = set()
        for other in segments:
            if other == segment:
                continue
            for point in ((other[0], other[1]), (other[2], other[3])):
                if self._point_on_segment(point, segment):
                    points.add(point)
            intersection = self._segment_intersection_point(segment, other)
            if intersection is not None:
                points.add(intersection)
        return points

    def _segment_intersection_points(self, segments: list[tuple[int, int, int, int]]) -> set[tuple[int, int]]:
        points: set[tuple[int, int]] = set()
        for index, first in enumerate(segments):
            for second in segments[index + 1 :]:
                point = self._segment_intersection_point(first, second)
                if point is not None:
                    points.add(point)
        return points

    def _segment_intersection_point(
        self,
        first: tuple[int, int, int, int],
        second: tuple[int, int, int, int],
    ) -> tuple[int, int] | None:
        f_horizontal = first[1] == first[3]
        f_vertical = first[0] == first[2]
        s_horizontal = second[1] == second[3]
        s_vertical = second[0] == second[2]
        if f_horizontal and s_vertical:
            point = (second[0], first[1])
        elif f_vertical and s_horizontal:
            point = (first[0], second[1])
        else:
            return None
        if self._point_on_segment(point, first) and self._point_on_segment(point, second):
            return point
        return None

    def _unrouted_fallback(self, start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int, int, int]]:
        return [(start[0], start[1], end[0], end[1])]

    def _detour_overlaps(
        self,
        segments: list[tuple[int, int, int, int]],
        used_edges: set[GridEdge],
        obstacles: list[tuple[int, int, int, int]],
    ) -> list[tuple[int, int, int, int]]:
        cleaned: list[tuple[int, int, int, int]] = []
        for segment in segments:
            if any(edge in used_edges for edge in self._segment_edges(segment)):
                cleaned.extend(self._detour_segment(segment, used_edges, obstacles))
            else:
                cleaned.append(segment)
        return [segment for segment in cleaned if (segment[0], segment[1]) != (segment[2], segment[3])]

    def _remove_global_overlaps(
        self,
        routes: list[RoutedNet],
        body_obstacles: list[tuple[int, int, int, int]],
    ) -> None:
        for _ in range(4):
            before = [tuple(route.segments) for route in routes]
            used_edges: set[GridEdge] = set()
            for route in sorted(routes, key=self._route_length, reverse=True):
                cleaned: list[tuple[int, int, int, int]] = []
                for segment in route.segments:
                    detoured = self._detour_overlaps([segment], used_edges, body_obstacles)
                    cleaned.extend(detoured)
                    for clean_segment in detoured:
                        self._reserve_segment(clean_segment, used_edges)
                route.segments = cleaned
            after = [tuple(route.segments) for route in routes]
            if after == before:
                break
        self._cleanup_in_order(routes, body_obstacles)

    def _cleanup_in_order(
        self,
        routes: list[RoutedNet],
        body_obstacles: list[tuple[int, int, int, int]],
    ) -> None:
        used_edges: set[GridEdge] = set()
        for route in routes:
            cleaned: list[tuple[int, int, int, int]] = []
            for segment in route.segments:
                detoured = self._detour_overlaps([segment], used_edges, body_obstacles)
                cleaned.extend(detoured)
                for clean_segment in detoured:
                    self._reserve_segment(clean_segment, used_edges)
            route.segments = cleaned

    def _segments_overlap(self, a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        if ay1 == ay2 == by1 == by2:
            return max(min(ax1, ax2), min(bx1, bx2)) < min(max(ax1, ax2), max(bx1, bx2))
        if ax1 == ax2 == bx1 == bx2:
            return max(min(ay1, ay2), min(by1, by2)) < min(max(ay1, ay2), max(by1, by2))
        return False

    def _route_length(self, route: RoutedNet) -> int:
        return sum(abs(x1 - x2) + abs(y1 - y2) for x1, y1, x2, y2 in route.segments)

    def _detour_segment(
        self,
        segment: tuple[int, int, int, int],
        used_edges: set[GridEdge],
        obstacles: list[tuple[int, int, int, int]],
    ) -> list[tuple[int, int, int, int]]:
        x1, y1, x2, y2 = segment
        if x1 == x2:
            for offset in self._detour_offsets():
                candidate = [(x1, y1, x1 + offset, y1), (x1 + offset, y1, x2 + offset, y2), (x2 + offset, y2, x2, y2)]
                if self._candidate_clear(candidate, used_edges, obstacles):
                    return candidate
            edge_detour = self._detour_segment_edges(segment, used_edges, obstacles)
            if edge_detour:
                return edge_detour
        if y1 == y2:
            for offset in self._detour_offsets():
                candidate = [(x1, y1, x1, y1 + offset), (x1, y1 + offset, x2, y2 + offset), (x2, y2 + offset, x2, y2)]
                if self._candidate_clear(candidate, used_edges, obstacles):
                    return candidate
            edge_detour = self._detour_segment_edges(segment, used_edges, obstacles)
            if edge_detour:
                return edge_detour
        return [segment]

    def _detour_segment_edges(
        self,
        segment: tuple[int, int, int, int],
        used_edges: set[GridEdge],
        obstacles: list[tuple[int, int, int, int]],
    ) -> list[tuple[int, int, int, int]] | None:
        x1, y1, x2, y2 = segment
        if x1 == x2:
            return self._detour_axis_edges((x1, y1), (x2, y2), used_edges, obstacles, vertical=True)
        if y1 == y2:
            return self._detour_axis_edges((x1, y1), (x2, y2), used_edges, obstacles, vertical=False)
        return None

    def _detour_axis_edges(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        used_edges: set[GridEdge],
        obstacles: list[tuple[int, int, int, int]],
        vertical: bool,
    ) -> list[tuple[int, int, int, int]] | None:
        segments: list[tuple[int, int, int, int]] = []
        current = start
        step = self.grid if (end[1] if vertical else end[0]) > (start[1] if vertical else start[0]) else -self.grid
        while current != end:
            next_point = (current[0], current[1] + step) if vertical else (current[0] + step, current[1])
            direct = (current[0], current[1], next_point[0], next_point[1])
            if self._edge(current, next_point) not in used_edges:
                segments.append(direct)
                current = next_point
                continue
            detour = self._edge_detour(current, next_point, used_edges, obstacles, vertical)
            if detour is None:
                return None
            segments.extend(detour)
            current = next_point
        return segments

    def _edge_detour(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        used_edges: set[GridEdge],
        obstacles: list[tuple[int, int, int, int]],
        vertical: bool,
    ) -> list[tuple[int, int, int, int]] | None:
        for offset in self._detour_offsets():
            if vertical:
                candidate = [(start[0], start[1], start[0] + offset, start[1]), (start[0] + offset, start[1], end[0] + offset, end[1]), (end[0] + offset, end[1], end[0], end[1])]
            else:
                candidate = [(start[0], start[1], start[0], start[1] + offset), (start[0], start[1] + offset, end[0], end[1] + offset), (end[0], end[1] + offset, end[0], end[1])]
            if self._candidate_clear(candidate, used_edges, obstacles):
                return candidate
        return None

    def _detour_offsets(self) -> tuple[int, ...]:
        return tuple(offset for distance in range(1, 31) for offset in (self.grid * distance, -self.grid * distance))

    def _candidate_clear(
        self,
        candidate: list[tuple[int, int, int, int]],
        used_edges: set[GridEdge],
        obstacles: list[tuple[int, int, int, int]],
    ) -> bool:
        return not any(
            edge in used_edges or self._segment_crosses_obstacle(part, obstacles)
            for part in candidate
            for edge in self._segment_edges(part)
        )

    def _segment_crosses_obstacle(
        self,
        segment: tuple[int, int, int, int],
        obstacles: list[tuple[int, int, int, int]],
    ) -> bool:
        x1, y1, x2, y2 = segment
        for bx1, by1, bx2, by2 in obstacles:
            if y1 == y2 and by1 < y1 < by2 and max(min(x1, x2), bx1) < min(max(x1, x2), bx2):
                return True
            if x1 == x2 and bx1 < x1 < bx2 and max(min(y1, y2), by1) < min(max(y1, y2), by2):
                return True
        return False

    def _reserve_segment(self, segment: tuple[int, int, int, int], used_edges: set[GridEdge]) -> None:
        for edge in self._segment_edges(segment):
            used_edges.add(edge)

    def _segment_edges(self, segment: tuple[int, int, int, int]) -> list[GridEdge]:
        x1, y1, x2, y2 = segment
        if x1 == x2:
            start, end = sorted((self._snap(y1), self._snap(y2)))
            return [self._edge((x1, y), (x1, y + self.grid)) for y in range(start, end, self.grid)]
        if y1 == y2:
            start, end = sorted((self._snap(x1), self._snap(x2)))
            return [self._edge((x, y1), (x + self.grid, y1)) for x in range(start, end, self.grid)]
        return []

    def _edge(self, a: tuple[int, int], b: tuple[int, int]) -> GridEdge:
        a = self._snap_point(a)
        b = self._snap_point(b)
        return (a, b) if a <= b else (b, a)

    def _edge_penalty(self, a: tuple[int, int], b: tuple[int, int], used_edges: set[GridEdge]) -> int:
        return self.overlap_penalty if self._edge(a, b) in used_edges else 0

    def _reconstruct(self, came_from: dict[tuple[int, int], tuple[int, int] | None], current: tuple[int, int]) -> list[tuple[int, int]]:
        path = [current]
        while came_from[current] is not None:
            current = came_from[current]
            path.append(current)
        path.reverse()
        return path

    def _blocked(self, point: tuple[int, int], obstacles: list[tuple[int, int, int, int]]) -> bool:
        x, y = point
        return any(x1 < x < x2 and y1 < y < y2 for x1, y1, x2, y2 in obstacles)

    def _in_bounds(self, point: tuple[int, int], bounds: tuple[int, int, int, int]) -> bool:
        x, y = point
        min_x, min_y, max_x, max_y = bounds
        return min_x <= x <= max_x and min_y <= y <= max_y

    def _snap_point(self, point: tuple[int, int]) -> tuple[int, int]:
        return (self._snap(point[0]), self._snap(point[1]))

    def _snap(self, value: int) -> int:
        return round(value / self.grid) * self.grid

    def _snap_down(self, value: int) -> int:
        return (value // self.grid) * self.grid

    def _snap_up(self, value: int) -> int:
        return ((value + self.grid - 1) // self.grid) * self.grid

    def _manhattan(self, a: tuple[int, int], b: tuple[int, int]) -> int:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])
