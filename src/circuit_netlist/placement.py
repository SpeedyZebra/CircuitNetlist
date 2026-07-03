from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .component_library import ComponentLibrary
from .constraint_placement import ConstraintPlacementOptimizer, PlacementOptimizationConfig, PlacementOptimizationResult, PlacementScore, RoutedLayoutEvaluation
from .geometry import component_size
from .models import Circuit, ComponentInstance, Layout, Placement
from .topology import ComponentRole, MIN_PLACEMENT_PATTERN_CONFIDENCE, PatternType, TopologyAnalysis, TopologyAnalyzer, TopologyPattern

MIN_COMPONENT_GAP = 40
MCU_CLEARANCE = 60


class PlacementEngine(Protocol):
    def place(self, circuit: Circuit, library: ComponentLibrary, existing: Layout | None = None) -> Layout:
        """Return component coordinates without changing electrical connectivity."""


@dataclass
class Bounds:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y


@dataclass
class FunctionalGroupEnvelope:
    group_id: str
    pattern_type: PatternType
    component_refs: list[str]
    body_min_x: float
    body_min_y: float
    body_max_x: float
    body_max_y: float
    expanded_min_x: float
    expanded_min_y: float
    expanded_max_x: float
    expanded_max_y: float
    margin_left: float
    margin_right: float
    margin_top: float
    margin_bottom: float
    local_placements: dict[str, Placement] = field(default_factory=dict)
    oriented_bounds: dict[str, Bounds] = field(default_factory=dict)
    fallback_refs: list[str] = field(default_factory=list)
    lane: int = 0
    origin_x: int = 0
    origin_y: int = 0

    @property
    def width(self) -> float:
        return self.expanded_max_x - self.expanded_min_x

    @property
    def height(self) -> float:
        return self.expanded_max_y - self.expanded_min_y

    def debug_dict(self) -> dict[str, object]:
        return {
            "group_id": self.group_id,
            "pattern_type": self.pattern_type.value,
            "component_refs": self.component_refs,
            "local_positions": {ref: placement.model_dump(mode="json") for ref, placement in self.local_placements.items()},
            "oriented_bounds": {ref: bounds.__dict__ for ref, bounds in self.oriented_bounds.items()},
            "body_union": {"min_x": self.body_min_x, "min_y": self.body_min_y, "max_x": self.body_max_x, "max_y": self.body_max_y},
            "margins": {"left": self.margin_left, "right": self.margin_right, "top": self.margin_top, "bottom": self.margin_bottom},
            "expanded_envelope": {"min_x": self.expanded_min_x, "min_y": self.expanded_min_y, "max_x": self.expanded_max_x, "max_y": self.expanded_max_y, "width": self.width, "height": self.height},
            "lane": self.lane,
            "origin": {"x": self.origin_x, "y": self.origin_y},
            "fallback_geometry_refs": self.fallback_refs,
        }


GROUP_CLEARANCE_X = 120
GROUP_CLEARANCE_Y = 120
PIN_ESCAPE_ALLOWANCE = 100
TEXT_CLEARANCE_ALLOWANCE = 60
POWER_SYMBOL_CLEARANCE = 90
FALLBACK_COMPONENT_WIDTH = 180
FALLBACK_COMPONENT_HEIGHT = 120


def get_oriented_component_bounds(
    component: ComponentInstance | None,
    x: int,
    y: int,
    orientation: int,
    library: ComponentLibrary,
) -> Bounds:
    definition = library.get(component.component_id) if component else None
    placement = Placement(x=x, y=y, rotation=orientation)
    if not definition or definition.body.width <= 0 or definition.body.height <= 0:
        return Bounds(x, y, x + FALLBACK_COMPONENT_WIDTH, y + FALLBACK_COMPONENT_HEIGHT)
    width, height = component_size(definition, placement)
    return Bounds(x, y, x + width, y + height)


@dataclass
class DeterministicPlacementEngine:
    grid: int = 40
    x_gap: int = 260
    y_gap: int = 170
    optimization_config: PlacementOptimizationConfig = field(default_factory=PlacementOptimizationConfig)

    def place(self, circuit: Circuit, library: ComponentLibrary, existing: Layout | None = None) -> Layout:
        layout = existing.model_copy(deep=True) if existing else Layout()
        self._existing_refs = set(layout.components)
        self._instances_by_ref = {component.ref: component for component in circuit.components}
        self.last_group_envelopes: list[dict[str, object]] = []
        layout.canvas["grid"] = self.grid
        analysis = TopologyAnalyzer().analyze(circuit, library)
        self._place_by_role(circuit, library, layout, analysis)
        self._place_patterns(circuit, library, layout, analysis)
        self._choose_two_pin_orientations(circuit, layout, analysis)
        self._fill_unplaced(circuit, library, layout, analysis)
        self._resize_canvas(circuit, library, layout)
        layout.canvas["functional_groups"] = self.last_group_envelopes
        layout = self._apply_constraint_optimizer(circuit, library, layout, analysis)
        return layout

    def _apply_constraint_optimizer(self, circuit: Circuit, library: ComponentLibrary, layout: Layout, analysis: TopologyAnalysis) -> Layout:
        optimizer = ConstraintPlacementOptimizer(self.optimization_config)
        result = optimizer.optimize(circuit, library, layout, analysis, fixed_refs=set(getattr(self, "_existing_refs", set())))
        optimized = result.optimized_layout
        optimized.canvas["functional_groups"] = layout.canvas.get("functional_groups", [])
        optimized.canvas["placement_optimizer"] = self._optimizer_metadata(result)
        return optimized

    def _optimizer_metadata(self, result: PlacementOptimizationResult) -> dict[str, object]:
        comparison = result.comparison
        return {
            "mode": self.optimization_config.mode,
            "optimize_soft_constraints": self.optimization_config.optimize_soft_constraints,
            "passes": comparison.passes,
            "candidate_evaluations": comparison.candidate_evaluations,
            "route_validations": comparison.route_validations,
            "rejected_candidates": len([report for report in comparison.candidate_reports if not report.get("accepted")]),
            "budget_reached": comparison.budget_reached,
            "budget_reason": comparison.budget_reason,
            "fast_path": comparison.fast_path,
            "initial": self._score_metadata(comparison.initial_score),
            "optimized": self._score_metadata(comparison.optimized_score),
            "initial_routed": self._evaluation_metadata(comparison.initial_evaluation),
            "optimized_routed": self._evaluation_metadata(comparison.optimized_evaluation),
            "moves": comparison.moves,
            "candidate_reports": [self._candidate_metadata(report) for report in comparison.candidate_reports],
        }

    def _score_metadata(self, score: PlacementScore) -> dict[str, object]:
        return {
            "total": round(float(score.total), 6),
            "hard_violation_count": score.hard_violation_count,
            "hard_penalty": round(float(score.hard_penalty), 6),
            "soft_penalty": round(float(score.total - score.hard_penalty), 6),
            "metrics": score.metrics,
            "violations": [
                {
                    "code": violation.code,
                    "severity": violation.severity.value,
                    "component_refs": violation.component_refs,
                    "penalty": round(float(violation.penalty), 6),
                    "explanation": violation.explanation,
                    "metadata": violation.metadata,
                }
                for violation in score.violations
            ],
        }

    def _evaluation_metadata(self, evaluation: RoutedLayoutEvaluation | None) -> dict[str, object] | None:
        if evaluation is None:
            return None
        return {
            "validation_level": evaluation.validation_level.value,
            "valid": evaluation.valid,
            "routing_succeeded": evaluation.routing_succeeded,
            "final_score": round(float(evaluation.final_score), 6),
            "route_count": evaluation.route_count,
            "total_routed_length": evaluation.total_routed_length,
            "total_bend_count": evaluation.total_bend_count,
            "wire_segment_count": evaluation.wire_segment_count,
            "max_net_length": evaluation.max_net_length,
            "average_net_length": evaluation.average_net_length,
            "scene_bounds": evaluation.scene_bounds.model_dump(mode="json"),
            "page_area": evaluation.page_area,
            "aspect_ratio": evaluation.aspect_ratio,
            "visual_diagnostic_codes": sorted(diag.code for diag in evaluation.visual_diagnostics if diag.code),
            "unexpected_visual_diagnostic_codes": sorted(diag.code for diag in evaluation.unexpected_visual_diagnostics if diag.code),
            "rejection_reasons": evaluation.rejection_reasons,
            "metrics": evaluation.metrics,
        }

    def _candidate_metadata(self, report: dict[str, object]) -> dict[str, object]:
        return {key: value for key, value in report.items() if key != "validation_time_ms"}

    def _place_by_role(self, circuit: Circuit, library: ComponentLibrary, layout: Layout, analysis: TopologyAnalysis) -> None:
        columns: dict[str, list[str]] = {"source": [], "power": [], "storage": [], "controller": [], "switch": [], "load": [], "passive": []}
        for component in circuit.components:
            if component.ref in layout.components:
                continue
            role = analysis.component_roles.get(component.ref, ComponentRole.UNKNOWN)
            column = self._role_bucket(component.component_id, role)
            columns[column].append(component.ref)
        x_for = {"source": 80, "power": 240, "storage": 460, "controller": 640, "switch": 1060, "load": 900, "passive": 780}
        for column, refs in columns.items():
            refs.sort()
            y = 140
            if column in {"power", "controller", "switch"}:
                y = 420
            if column == "passive":
                y = 760
            for ref in refs:
                layout.components[ref] = Placement(x=self._snap(x_for[column]), y=self._snap(y))
                instance = next(component for component in circuit.components if component.ref == ref)
                definition = library.get(instance.component_id)
                height = component_size(definition, layout.components[ref])[1] if definition else 100
                y += height + self.y_gap

    def _place_patterns(self, circuit: Circuit, library: ComponentLibrary, layout: Layout, analysis: TopologyAnalysis) -> None:
        low_side_refs = self._refs_in_patterns(analysis, PatternType.LOW_SIDE_MOSFET_SWITCH)
        self._place_led_current_limits(circuit, library, layout, analysis, low_side_refs)
        self._place_gate_resistors(library, layout, analysis, low_side_refs)
        self._place_low_side_switches(circuit, library, layout, analysis)
        self._place_voltage_dividers(circuit, library, layout, analysis)
        self._place_pull_resistors(layout, analysis)
        self._place_decoupling(circuit, library, layout, analysis)
        self._place_rc_filters(library, layout, analysis)

    def _place_led_current_limits(self, circuit: Circuit, library: ComponentLibrary, layout: Layout, analysis: TopologyAnalysis, low_side_refs: set[str]) -> None:
        lane_y = 320
        for lane, pattern in enumerate(self._strong_patterns(analysis, PatternType.LED_CURRENT_LIMIT)):
            resistor = pattern.metadata.get("resistor")
            led = pattern.metadata.get("led")
            if isinstance(resistor, str) and resistor in low_side_refs:
                continue
            if isinstance(led, str) and led in low_side_refs:
                continue
            shared_net = str(pattern.metadata.get("shared_net", ""))
            led_left = self._led_pin_net(circuit, led, "A") == shared_net if isinstance(led, str) else True
            local = self._led_limit_local_placements(library, resistor if isinstance(resistor, str) else None, led if isinstance(led, str) else None, led_left)
            envelope = self._build_envelope(pattern, library, local, margin_left=120, margin_right=160, margin_top=180, margin_bottom=180, lane=lane, origin_x=760, origin_y=lane_y)
            self._place_envelope(layout, envelope, 760, lane_y)
            lane_y = self._snap(int(lane_y + envelope.height + GROUP_CLEARANCE_Y))
            if led_left:
                pass
            else:
                pass
            for switch_ref in self._switches_on_nets(circuit, set(pattern.net_names)):
                self._set(layout, switch_ref, envelope.origin_x + int(envelope.body_max_x) + 160, envelope.origin_y + int(envelope.body_min_y) + 100, 0)

    def _place_gate_resistors(self, library: ComponentLibrary, layout: Layout, analysis: TopologyAnalysis, low_side_refs: set[str]) -> None:
        lane_y = 400
        for lane, pattern in enumerate(self._strong_patterns(analysis, PatternType.SERIES_GATE_RESISTOR)):
            driver = pattern.metadata.get("driver_component")
            resistor = pattern.metadata.get("gate_resistor")
            mosfet = pattern.metadata.get("mosfet")
            if isinstance(mosfet, str) and mosfet in low_side_refs:
                continue
            local = self._gate_resistor_local_placements(library, driver if isinstance(driver, str) else None, resistor if isinstance(resistor, str) else None, mosfet if isinstance(mosfet, str) else None)
            envelope = self._build_envelope(pattern, library, local, margin_left=160, margin_right=180, margin_top=180, margin_bottom=180, lane=lane, origin_x=520, origin_y=lane_y)
            self._place_envelope(layout, envelope, 520, lane_y)
            lane_y = self._snap(int(lane_y + envelope.height + GROUP_CLEARANCE_Y))

    def _place_low_side_switches(self, circuit: Circuit, library: ComponentLibrary, layout: Layout, analysis: TopologyAnalysis) -> None:
        lane_y = 260
        placed_drivers: set[str] = set()
        for lane, pattern in enumerate(self._strong_patterns(analysis, PatternType.LOW_SIDE_MOSFET_SWITCH)):
            mosfet = str(pattern.metadata.get("mosfet", ""))
            if not mosfet:
                continue
            locked_mosfet = layout.components.get(mosfet) if self._is_locked(layout, mosfet) else None
            gate_resistor = pattern.metadata.get("gate_resistor")
            pull_down = pattern.metadata.get("pull_down")
            load_pattern = pattern.metadata.get("load_pattern")
            load_resistor = None
            led = None
            if isinstance(load_pattern, dict):
                load_resistor = load_pattern.get("metadata", {}).get("resistor")
                led = load_pattern.get("metadata", {}).get("led")
            local = self._low_side_local_placements(
                library,
                mosfet,
                gate_resistor if isinstance(gate_resistor, str) else None,
                pull_down if isinstance(pull_down, str) else None,
                load_resistor if isinstance(load_resistor, str) else None,
                led if isinstance(led, str) else None,
            )
            driver = next((ref for ref in pattern.component_refs if analysis.component_roles.get(ref) == ComponentRole.CONTROLLER), None)
            origin_x = 760
            if driver:
                driver_width, _ = self._component_dimensions(library, driver, 0)
                origin_x = max(origin_x, 560 + driver_width + 120)
            envelope = self._build_envelope(pattern, library, local, margin_left=180, margin_right=220, margin_top=180, margin_bottom=220, lane=lane, origin_x=origin_x, origin_y=lane_y)
            if locked_mosfet:
                mosfet_local = envelope.local_placements.get(mosfet, Placement(x=0, y=0))
                origin_x = locked_mosfet.x - mosfet_local.x
                origin_y = locked_mosfet.y - mosfet_local.y
            else:
                origin_y = lane_y
            self._place_envelope(layout, envelope, origin_x, origin_y)
            lane_y = self._snap(int(lane_y + envelope.height + GROUP_CLEARANCE_Y))
            if driver and driver not in placed_drivers:
                self._set(layout, driver, 560, origin_y + 160, 0)
                placed_drivers.add(driver)

    def _place_voltage_dividers(self, circuit: Circuit, library: ComponentLibrary, layout: Layout, analysis: TopologyAnalysis) -> None:
        patterns = self._strong_patterns(analysis, PatternType.VOLTAGE_DIVIDER)
        lane_x = 1880
        for lane, pattern in enumerate(patterns):
            top = pattern.metadata.get("top_resistor")
            bottom = pattern.metadata.get("bottom_resistor")
            local = self._divider_local_placements(library, top if isinstance(top, str) else None, bottom if isinstance(bottom, str) else None)
            envelope = self._build_envelope(pattern, library, local, margin_left=100, margin_right=120, margin_top=120, margin_bottom=140, lane=lane, origin_x=lane_x, origin_y=620)
            self._place_envelope(layout, envelope, lane_x, 620)
            lane_x = self._snap(int(lane_x + envelope.width + GROUP_CLEARANCE_X))

    def _place_pull_resistors(self, layout: Layout, analysis: TopologyAnalysis) -> None:
        low_side_pull_refs = {
            pull_down
            for low_side in self._strong_patterns(analysis, PatternType.LOW_SIDE_MOSFET_SWITCH)
            for pull_down in [low_side.metadata.get("pull_down")]
            if isinstance(pull_down, str)
        }
        occupied = 0
        for pattern in [*self._strong_patterns(analysis, PatternType.PULL_DOWN), *self._strong_patterns(analysis, PatternType.PULL_UP)]:
            resistor = pattern.metadata.get("resistor")
            if not isinstance(resistor, str):
                continue
            if resistor in low_side_pull_refs:
                continue
            if pattern.pattern_type == PatternType.PULL_DOWN:
                self._set(layout, resistor, 800 + occupied * 180, 780, 90)
            else:
                self._set(layout, resistor, 800 + occupied * 180, 260, 90)
            occupied += 1

    def _place_decoupling(self, circuit: Circuit, library: ComponentLibrary, layout: Layout, analysis: TopologyAnalysis) -> None:
        target_slots: dict[str, int] = {}
        target_offsets: dict[str, int] = {}
        fallback_slot = 0
        for pattern in self._strong_patterns(analysis, PatternType.DECOUPLING_CAPACITOR):
            capacitor = pattern.metadata.get("capacitor")
            target = pattern.metadata.get("target_component")
            if not isinstance(capacitor, str):
                continue
            if isinstance(target, str) and target in layout.components:
                slot = target_slots.get(target, 0)
                target_slots[target] = slot + 1
                offset = target_offsets.get(target, 0)
                target_placement = layout.components[target]
                target_instance = next((component for component in circuit.components if component.ref == target), None)
                target_definition = library.get(target_instance.component_id) if target_instance else None
                target_height = component_size(target_definition, target_placement)[1] if target_definition else 160
                cap_width, _ = self._component_dimensions(library, capacitor, 90)
                self._set(layout, capacitor, target_placement.x - 240 + offset, target_placement.y + target_height + 180, 90)
                target_offsets[target] = offset + cap_width + 140
            else:
                fallback_slot += 1
                placement = layout.components.get(capacitor)
                if placement and not placement.locked and capacitor not in getattr(self, "_existing_refs", set()):
                    self._set(layout, capacitor, placement.x + fallback_slot * 140, placement.y, 90)
        self._place_bulk_capacitors(library, layout, analysis, target_slots)

    def _place_rc_filters(self, library: ComponentLibrary, layout: Layout, analysis: TopologyAnalysis) -> None:
        lane_y = 520
        for lane, pattern in enumerate(self._strong_patterns(analysis, PatternType.RC_LOWPASS)):
            resistor = pattern.metadata.get("resistor")
            capacitor = pattern.metadata.get("capacitor")
            local = self._rc_filter_local_placements(library, resistor if isinstance(resistor, str) else None, capacitor if isinstance(capacitor, str) else None)
            envelope = self._build_envelope(pattern, library, local, margin_left=140, margin_right=160, margin_top=180, margin_bottom=220, lane=lane, origin_x=800, origin_y=lane_y)
            self._place_envelope(layout, envelope, 800, lane_y)
            lane_y = self._snap(int(lane_y + envelope.height + GROUP_CLEARANCE_Y))

    def _choose_two_pin_orientations(self, circuit: Circuit, layout: Layout, analysis: TopologyAnalysis) -> None:
        orientation_by_ref: dict[str, int] = {}
        for pattern in analysis.patterns:
            if pattern.confidence < MIN_PLACEMENT_PATTERN_CONFIDENCE:
                continue
            if pattern.pattern_type in {PatternType.VOLTAGE_DIVIDER, PatternType.PULL_DOWN, PatternType.PULL_UP, PatternType.DECOUPLING_CAPACITOR}:
                refs = pattern.component_refs
                if pattern.pattern_type == PatternType.DECOUPLING_CAPACITOR:
                    capacitor = pattern.metadata.get("capacitor")
                    refs = [capacitor] if isinstance(capacitor, str) else []
                for ref in refs:
                    orientation_by_ref.setdefault(ref, 90)
            if pattern.pattern_type in {PatternType.SERIES_GATE_RESISTOR, PatternType.LED_CURRENT_LIMIT, PatternType.SERIES_ELEMENT, PatternType.RC_LOWPASS}:
                refs = pattern.component_refs
                if pattern.pattern_type == PatternType.SERIES_GATE_RESISTOR:
                    gate_resistor = pattern.metadata.get("gate_resistor")
                    refs = [gate_resistor] if isinstance(gate_resistor, str) else []
                if pattern.pattern_type == PatternType.LED_CURRENT_LIMIT:
                    resistor = pattern.metadata.get("resistor")
                    refs = [resistor] if isinstance(resistor, str) else []
                if pattern.pattern_type == PatternType.RC_LOWPASS:
                    resistor = pattern.metadata.get("resistor")
                    refs = [resistor] if isinstance(resistor, str) else []
                for ref in refs:
                    orientation_by_ref.setdefault(ref, 0)
            if pattern.pattern_type == PatternType.RC_LOWPASS:
                capacitor = pattern.metadata.get("capacitor")
                if isinstance(capacitor, str):
                    orientation_by_ref[capacitor] = 90
        for component in circuit.components:
            placement = layout.components.get(component.ref)
            if not placement or placement.locked:
                continue
            if component.ref in getattr(self, "_existing_refs", set()):
                continue
            if component.ref in orientation_by_ref:
                placement.rotation = orientation_by_ref[component.ref]

    def _fill_unplaced(self, circuit: Circuit, library: ComponentLibrary, layout: Layout, analysis: TopologyAnalysis) -> None:
        # Kept as a separate hook for later scored placement milestones.
        for component in sorted(circuit.components, key=lambda item: item.ref):
            if component.ref in layout.components:
                continue
            role = analysis.component_roles.get(component.ref, ComponentRole.UNKNOWN)
            column = self._role_bucket(component.component_id, role)
            x_for = {"source": 80, "power": 240, "storage": 460, "controller": 640, "switch": 1060, "load": 900, "passive": 780}
            y = 140 + len(layout.components) * self.y_gap
            layout.components[component.ref] = Placement(x=self._snap(x_for[column]), y=self._snap(y))

    def _role_bucket(self, component_id: str, role: ComponentRole) -> str:
        if role == ComponentRole.SOURCE:
            return "source"
        if role in {ComponentRole.CHARGER, ComponentRole.REGULATOR}:
            return "power"
        if role == ComponentRole.STORAGE:
            return "storage"
        if role == ComponentRole.CONTROLLER:
            return "controller"
        if role == ComponentRole.SWITCH:
            return "switch"
        if role == ComponentRole.LOAD:
            return "load"
        return "passive"

    def _strong_patterns(self, analysis: TopologyAnalysis, pattern_type: PatternType) -> list[TopologyPattern]:
        return [pattern for pattern in analysis.patterns if pattern.pattern_type == pattern_type and pattern.confidence >= MIN_PLACEMENT_PATTERN_CONFIDENCE]

    def _refs_in_patterns(self, analysis: TopologyAnalysis, pattern_type: PatternType) -> set[str]:
        return {
            ref
            for pattern in self._strong_patterns(analysis, pattern_type)
            for ref in pattern.component_refs
            if isinstance(ref, str)
        }

    def _build_envelope(
        self,
        pattern: TopologyPattern,
        library: ComponentLibrary,
        local_placements: dict[str, Placement],
        *,
        margin_left: int,
        margin_right: int,
        margin_top: int,
        margin_bottom: int,
        lane: int,
        origin_x: int,
        origin_y: int,
    ) -> FunctionalGroupEnvelope:
        oriented: dict[str, Bounds] = {}
        fallback_refs: list[str] = []
        for ref, placement in local_placements.items():
            instance = getattr(self, "_instances_by_ref", {}).get(ref)
            definition = library.get(instance.component_id) if instance else None
            if not definition or definition.body.width <= 0 or definition.body.height <= 0:
                fallback_refs.append(ref)
            oriented[ref] = get_oriented_component_bounds(instance, placement.x, placement.y, placement.rotation, library)
        if not oriented:
            oriented["_fallback"] = Bounds(0, 0, FALLBACK_COMPONENT_WIDTH, FALLBACK_COMPONENT_HEIGHT)
            fallback_refs.append("_fallback")
        body_min_x = min(bounds.min_x for bounds in oriented.values())
        body_min_y = min(bounds.min_y for bounds in oriented.values())
        body_max_x = max(bounds.max_x for bounds in oriented.values())
        body_max_y = max(bounds.max_y for bounds in oriented.values())
        expanded_min_x = body_min_x - margin_left
        expanded_min_y = body_min_y - margin_top
        expanded_max_x = body_max_x + margin_right
        expanded_max_y = body_max_y + margin_bottom
        normalized_placements = {
            ref: Placement(x=self._snap(int(placement.x - expanded_min_x)), y=self._snap(int(placement.y - expanded_min_y)), rotation=placement.rotation)
            for ref, placement in local_placements.items()
        }
        normalized_bounds = {
            ref: Bounds(bounds.min_x - expanded_min_x, bounds.min_y - expanded_min_y, bounds.max_x - expanded_min_x, bounds.max_y - expanded_min_y)
            for ref, bounds in oriented.items()
        }
        return FunctionalGroupEnvelope(
            group_id=str(pattern.metadata.get("mosfet") or ":".join(local_placements) or ":".join(pattern.component_refs)),
            pattern_type=pattern.pattern_type,
            component_refs=sorted(local_placements),
            body_min_x=body_min_x - expanded_min_x,
            body_min_y=body_min_y - expanded_min_y,
            body_max_x=body_max_x - expanded_min_x,
            body_max_y=body_max_y - expanded_min_y,
            expanded_min_x=0,
            expanded_min_y=0,
            expanded_max_x=expanded_max_x - expanded_min_x,
            expanded_max_y=expanded_max_y - expanded_min_y,
            margin_left=margin_left,
            margin_right=margin_right,
            margin_top=margin_top,
            margin_bottom=margin_bottom,
            local_placements=normalized_placements,
            oriented_bounds=normalized_bounds,
            fallback_refs=fallback_refs,
            lane=lane,
            origin_x=self._snap(origin_x),
            origin_y=self._snap(origin_y),
        )

    def _place_envelope(self, layout: Layout, envelope: FunctionalGroupEnvelope, origin_x: int, origin_y: int) -> None:
        envelope.origin_x = self._snap(origin_x)
        envelope.origin_y = self._snap(origin_y)
        self.last_group_envelopes.append(envelope.debug_dict())
        for ref, placement in envelope.local_placements.items():
            self._set(layout, ref, envelope.origin_x + placement.x, envelope.origin_y + placement.y, placement.rotation)

    def _component_dimensions(self, library: ComponentLibrary, ref: str, rotation: int) -> tuple[int, int]:
        instance = getattr(self, "_instances_by_ref", {}).get(ref)
        definition = library.get(instance.component_id) if instance else None
        if not definition or definition.body.width <= 0 or definition.body.height <= 0:
            return (FALLBACK_COMPONENT_WIDTH, FALLBACK_COMPONENT_HEIGHT)
        return component_size(definition, Placement(x=0, y=0, rotation=rotation))

    def _led_limit_local_placements(self, library: ComponentLibrary, resistor: str | None, led: str | None, led_left: bool) -> dict[str, Placement]:
        local: dict[str, Placement] = {}
        resistor_w, _ = self._component_dimensions(library, resistor, 0) if resistor else (140, 80)
        led_w, _ = self._component_dimensions(library, led, 0) if led else (140, 100)
        if led_left:
            if resistor:
                local[resistor] = Placement(x=0, y=0, rotation=0)
            if led:
                local[led] = Placement(x=resistor_w + 120, y=0, rotation=0)
        else:
            if led:
                local[led] = Placement(x=0, y=0, rotation=0)
            if resistor:
                local[resistor] = Placement(x=led_w + 120, y=0, rotation=0)
        return local

    def _gate_resistor_local_placements(self, library: ComponentLibrary, driver: str | None, resistor: str | None, mosfet: str | None) -> dict[str, Placement]:
        local: dict[str, Placement] = {}
        driver_w, _ = self._component_dimensions(library, driver, 0) if driver else (160, 200)
        resistor_w, _ = self._component_dimensions(library, resistor, 0) if resistor else (140, 80)
        if driver:
            local[driver] = Placement(x=0, y=0, rotation=0)
        if resistor:
            local[resistor] = Placement(x=driver_w + 260, y=120, rotation=0)
        if mosfet:
            local[mosfet] = Placement(x=driver_w + resistor_w + 420, y=0, rotation=0)
        return local

    def _low_side_local_placements(
        self,
        library: ComponentLibrary,
        mosfet: str,
        gate_resistor: str | None,
        pull_down: str | None,
        load_resistor: str | None,
        led: str | None,
    ) -> dict[str, Placement]:
        local: dict[str, Placement] = {}
        load_w, load_h = self._component_dimensions(library, load_resistor, 0) if load_resistor else (140, 80)
        led_w, led_h = self._component_dimensions(library, led, 0) if led else (140, 100)
        gate_w, gate_h = self._component_dimensions(library, gate_resistor, 0) if gate_resistor else (140, 80)
        mos_w, mos_h = self._component_dimensions(library, mosfet, 0)
        pull_w, _ = self._component_dimensions(library, pull_down, 90) if pull_down else (80, 140)
        load_gap = 120
        branch_to_switch_gap = 180
        gate_gap = 180
        mos_x = max(load_w + load_gap + led_w + branch_to_switch_gap, gate_w + gate_gap)
        branch_y = 0
        mos_y = max(load_h, led_h) + 160
        if load_resistor:
            local[load_resistor] = Placement(x=0, y=branch_y, rotation=0)
        if led:
            local[led] = Placement(x=load_w + load_gap, y=branch_y, rotation=0)
        local[mosfet] = Placement(x=mos_x, y=mos_y, rotation=0)
        if gate_resistor:
            local[gate_resistor] = Placement(x=mos_x - gate_w - gate_gap, y=mos_y, rotation=0)
        if pull_down:
            pull_x = max(0, mos_x - gate_gap + (gate_w - pull_w) // 2)
            local[pull_down] = Placement(x=pull_x, y=mos_y + mos_h + 120, rotation=90)
        return local

    def _divider_local_placements(self, library: ComponentLibrary, top: str | None, bottom: str | None) -> dict[str, Placement]:
        local: dict[str, Placement] = {}
        _, top_h = self._component_dimensions(library, top, 90) if top else (80, 140)
        if top:
            local[top] = Placement(x=0, y=0, rotation=90)
        if bottom:
            local[bottom] = Placement(x=0, y=top_h + 140, rotation=90)
        return local

    def _rc_filter_local_placements(self, library: ComponentLibrary, resistor: str | None, capacitor: str | None) -> dict[str, Placement]:
        local: dict[str, Placement] = {}
        resistor_w, resistor_h = self._component_dimensions(library, resistor, 0) if resistor else (140, 80)
        if resistor:
            local[resistor] = Placement(x=0, y=0, rotation=0)
        if capacitor:
            local[capacitor] = Placement(x=resistor_w + 160, y=resistor_h + 140, rotation=90)
        return local

    def _place_bulk_capacitors(self, library: ComponentLibrary, layout: Layout, analysis: TopologyAnalysis, target_slots: dict[str, int]) -> None:
        role_rank = {"bulk": 0, "reservoir": 1, "unknown_power_shunt": 2}
        bulk_slots: dict[str, int] = {}
        bulk_offsets: dict[str, int] = {}
        refs = [
            ref
            for ref, metadata in sorted(analysis.capacitor_role_metadata.items(), key=lambda item: (role_rank.get(str(item[1].get("role")), 9), item[0]))
            if metadata.get("role") in {"bulk", "reservoir", "unknown_power_shunt"} and ref in layout.components
        ]
        for index, ref in enumerate(refs):
            placement = layout.components.get(ref)
            if not placement or placement.locked or ref in getattr(self, "_existing_refs", set()):
                continue
            target = analysis.capacitor_role_metadata.get(ref, {}).get("target_component")
            if isinstance(target, str) and target in layout.components:
                slot = bulk_slots.get(target, 0)
                bulk_slots[target] = slot + 1
                offset = bulk_offsets.get(target, 0)
                target_placement = layout.components[target]
                cap_width, _ = self._component_dimensions(library, ref, 90)
                self._set(layout, ref, target_placement.x - 520 + offset, target_placement.y + 1100, 90)
                bulk_offsets[target] = offset + cap_width + 140
            else:
                self._set(layout, ref, 300 + index * 160, 520, 90)

    def _set(self, layout: Layout, ref: str, x: int, y: int, rotation: int) -> None:
        existing = layout.components.get(ref)
        if existing and (existing.locked or ref in getattr(self, "_existing_refs", set())):
            return
        if existing:
            existing.x = self._snap(x)
            existing.y = self._snap(y)
            existing.rotation = rotation
            return
        layout.components[ref] = Placement(x=self._snap(x), y=self._snap(y), rotation=rotation)

    def _is_locked(self, layout: Layout, ref: str) -> bool:
        placement = layout.components.get(ref)
        return bool((placement and placement.locked) or ref in getattr(self, "_existing_refs", set()))

    def _led_pin_net(self, circuit: Circuit, ref: object, pin_name: str) -> str | None:
        if not isinstance(ref, str):
            return None
        for net in circuit.nets:
            if any(pin.component_ref == ref and pin.pin_name == pin_name for pin in net.pins):
                return net.name
        return None

    def _switches_on_nets(self, circuit: Circuit, net_names: set[str]) -> list[str]:
        refs: list[str] = []
        for component in sorted(circuit.components, key=lambda item: item.ref):
            if component.component_id not in {"BASIC_NMOS", "BASIC_PMOS"}:
                continue
            connected = {net.name for net in circuit.nets for pin in net.pins if pin.component_ref == component.ref}
            if connected & net_names:
                refs.append(component.ref)
        return refs

    def _snap(self, value: int) -> int:
        return round(value / self.grid) * self.grid

    def _resize_canvas(self, circuit: Circuit, library: ComponentLibrary, layout: Layout) -> None:
        max_x = 0
        max_y = 0
        for component in circuit.components:
            placement = layout.components.get(component.ref)
            definition = library.get(component.component_id)
            if not placement or not definition:
                continue
            width, height = component_size(definition, placement)
            max_x = max(max_x, placement.x + width)
            max_y = max(max_y, placement.y + height)
        layout.canvas["width"] = max(int(layout.canvas.get("width", 0)), self._snap(max_x + 220))
        layout.canvas["height"] = max(int(layout.canvas.get("height", 0)), self._snap(max_y + 220))
