from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from enum import Enum
from time import perf_counter
from typing import Any, Iterable

from .component_library import ComponentLibrary
from .geometry import absolute_pin_point, boxes_overlap, component_body_box, component_size, inflate_box, is_orientation_sensitive, segment_crosses_box, segments_collinear_overlap, visual_pin_side
from .models import Circuit, Diagnostic, Layout, Placement, Severity
from .router import ManhattanRouter
from .scene import Bounds, SchematicScene
from .scene_builder import build_schematic_scene
from .topology import ComponentRole, MIN_PLACEMENT_PATTERN_CONFIDENCE, PatternType, TopologyAnalysis, TopologyAnalyzer, TopologyPattern


class ConstraintSeverity(str, Enum):
    HARD = "hard"
    SOFT = "soft"


class CandidateValidationLevel(str, Enum):
    GEOMETRY_ONLY = "geometry_only"
    ROUTED = "routed"
    SCENE_VALIDATED = "scene_validated"


@dataclass(frozen=True)
class PlacementViolation:
    code: str
    severity: ConstraintSeverity
    component_refs: list[str]
    penalty: float
    explanation: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PlacementWeights:
    hard_violation: float = 1_000_000.0
    component_overlap: float = 1_000_000.0
    unique_position: float = 500_000.0
    locked_movement: float = 1_000_000.0
    locked_obstacle: float = 750_000.0
    outside_canvas: float = 250_000.0
    clearance: float = 50.0
    estimated_wire_length: float = 1.0
    estimated_bend: float = 24.0
    wrong_facing: float = 60.0
    flow_reversal: float = 140.0
    group_separation: float = 0.2
    alignment: float = 0.15
    page_area: float = 0.0008
    aspect_ratio: float = 350.0
    vertical_spread: float = 0.08
    horizontal_spread: float = 0.04
    controller_distance: float = 0.35


DEFAULT_PLACEMENT_WEIGHTS = PlacementWeights()


@dataclass(frozen=True)
class RoutedLayoutWeights:
    visual_drc_penalty: float = 1_000_000.0
    routing_failure_penalty: float = 1_000_000.0
    actual_wire_length_weight: float = 1.0
    actual_bend_weight: float = 60.0
    max_net_length_weight: float = 0.2
    page_area_weight: float = 0.0005
    aspect_ratio_weight: float = 280.0
    displacement_weight: float = 0.1
    orientation_change_weight: float = 160.0


DEFAULT_ROUTED_LAYOUT_WEIGHTS = RoutedLayoutWeights()


@dataclass(frozen=True)
class PlacementOptimizationConfig:
    mode: str = "optimize"
    grid: int = 40
    max_passes: int = 2
    max_candidates_per_unit: int = 25
    max_total_evaluations: int = 600
    max_prefilter_candidates: int = 4
    max_route_validations_per_pass: int = 2
    max_total_route_validations: int = 6
    max_optimization_time_ms: int = 300
    candidate_radii: tuple[int, ...] = (2, 4, 6)
    minimum_component_clearance: int = 24
    preferred_aspect_min: float = 1.0
    preferred_aspect_max: float = 1.8
    optimize_soft_constraints: bool = True
    enable_orientation_candidates: bool = True
    min_final_score_improvement: float = 1.0
    weights: PlacementWeights = DEFAULT_PLACEMENT_WEIGHTS
    routed_weights: RoutedLayoutWeights = DEFAULT_ROUTED_LAYOUT_WEIGHTS


@dataclass(frozen=True)
class PlacementScore:
    total: float
    hard_violation_count: int
    hard_penalty: float
    overlap_penalty: float
    clearance_penalty: float
    estimated_wire_length_penalty: float
    estimated_bend_penalty: float
    flow_penalty: float
    group_separation_penalty: float
    alignment_penalty: float
    page_area_penalty: float
    aspect_ratio_penalty: float
    locked_obstacle_penalty: float
    violations: list[PlacementViolation]
    metrics: dict[str, float]

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["violations"] = [
            {**asdict(violation), "severity": violation.severity.value}
            for violation in self.violations
        ]
        return data


@dataclass(frozen=True)
class RoutedLayoutEvaluation:
    placement_score: PlacementScore
    validation_level: CandidateValidationLevel
    routing_succeeded: bool
    route_count: int
    total_routed_length: float
    total_bend_count: int
    wire_segment_count: int
    net_count: int
    max_net_length: float
    average_net_length: float
    scene_bounds: Bounds
    page_area: float
    aspect_ratio: float
    component_utilization_ratio: float
    visual_diagnostics: list[Diagnostic]
    unexpected_visual_diagnostics: list[Diagnostic]
    expected_visual_diagnostics: list[Diagnostic]
    valid: bool
    final_score: float
    rejection_reasons: list[str]
    metrics: dict[str, float]

    def to_dict(self) -> dict[str, object]:
        return {
            "placement_score": self.placement_score.to_dict(),
            "validation_level": self.validation_level.value,
            "routing_succeeded": self.routing_succeeded,
            "route_count": self.route_count,
            "total_routed_length": self.total_routed_length,
            "total_bend_count": self.total_bend_count,
            "wire_segment_count": self.wire_segment_count,
            "net_count": self.net_count,
            "max_net_length": self.max_net_length,
            "average_net_length": self.average_net_length,
            "scene_bounds": self.scene_bounds.model_dump(mode="json"),
            "page_area": self.page_area,
            "aspect_ratio": self.aspect_ratio,
            "component_utilization_ratio": self.component_utilization_ratio,
            "visual_diagnostics": [diag.model_dump(mode="json") for diag in self.visual_diagnostics],
            "unexpected_visual_diagnostics": [diag.model_dump(mode="json") for diag in self.unexpected_visual_diagnostics],
            "expected_visual_diagnostics": [diag.model_dump(mode="json") for diag in self.expected_visual_diagnostics],
            "valid": self.valid,
            "final_score": self.final_score,
            "rejection_reasons": self.rejection_reasons,
            "metrics": self.metrics,
        }


@dataclass(frozen=True)
class PlacementComparison:
    initial_score: PlacementScore
    optimized_score: PlacementScore
    passes: int
    candidate_evaluations: int
    route_validations: int
    elapsed_ms: float
    budget_reached: bool
    budget_reason: str | None
    fast_path: str | None
    moves: list[dict[str, object]]
    candidate_reports: list[dict[str, object]] = field(default_factory=list)
    initial_evaluation: RoutedLayoutEvaluation | None = None
    optimized_evaluation: RoutedLayoutEvaluation | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "initial_score": self.initial_score.to_dict(),
            "optimized_score": self.optimized_score.to_dict(),
            "passes": self.passes,
            "candidate_evaluations": self.candidate_evaluations,
            "route_validations": self.route_validations,
            "elapsed_ms": self.elapsed_ms,
            "budget_reached": self.budget_reached,
            "budget_reason": self.budget_reason,
            "fast_path": self.fast_path,
            "moves": self.moves,
            "candidate_reports": self.candidate_reports,
            "initial_evaluation": self.initial_evaluation.to_dict() if self.initial_evaluation else None,
            "optimized_evaluation": self.optimized_evaluation.to_dict() if self.optimized_evaluation else None,
        }


@dataclass(frozen=True)
class PlacementOptimizationResult:
    initial_layout: Layout
    optimized_layout: Layout
    comparison: PlacementComparison


@dataclass(frozen=True)
class MoveUnit:
    unit_id: str
    refs: tuple[str, ...]


@dataclass(frozen=True)
class CandidateProposal:
    candidate_id: str
    unit: MoveUnit
    layout: Layout
    pre_score: PlacementScore
    transformation: dict[str, object]
    displacement: float
    orientation_changes: int = 0


class PlacementScorer:
    def __init__(self, weights: PlacementWeights = DEFAULT_PLACEMENT_WEIGHTS, minimum_clearance: int = 24, preferred_aspect: tuple[float, float] = (1.0, 1.8)) -> None:
        self.weights = weights
        self.minimum_clearance = minimum_clearance
        self.preferred_aspect = preferred_aspect

    def score(
        self,
        circuit: Circuit,
        library: ComponentLibrary,
        layout: Layout,
        analysis: TopologyAnalysis | None = None,
        *,
        fixed_refs: set[str] | None = None,
        fixed_layout: Layout | None = None,
    ) -> PlacementScore:
        analysis = analysis or TopologyAnalyzer().analyze(circuit, library)
        fixed_refs = fixed_refs or set()
        boxes = _component_boxes(circuit, library, layout)
        centers = {ref: _box_center(box) for ref, box in boxes.items()}
        violations: list[PlacementViolation] = []

        hard_penalty = 0.0
        overlap_penalty = 0.0
        locked_obstacle_penalty = 0.0

        refs = sorted(boxes)
        for index, ref_a in enumerate(refs):
            for ref_b in refs[index + 1 :]:
                a = boxes[ref_a]
                b = boxes[ref_b]
                if boxes_overlap(a, b):
                    penalty = self.weights.component_overlap
                    overlap_penalty += penalty
                    hard_penalty += penalty
                    violations.append(
                        PlacementViolation("NO_COMPONENT_BODY_OVERLAP", ConstraintSeverity.HARD, [ref_a, ref_b], penalty, "Component body boxes overlap.", {"a": a, "b": b})
                    )
                elif boxes_overlap(inflate_box(a, self.minimum_clearance), inflate_box(b, self.minimum_clearance)):
                    clearance = _box_clearance(a, b)
                    penalty = (self.minimum_clearance - clearance) * self.weights.clearance
                    violations.append(
                        PlacementViolation("MINIMUM_COMPONENT_CLEARANCE", ConstraintSeverity.SOFT, [ref_a, ref_b], penalty, "Component clearance is below the preferred minimum.", {"clearance": clearance})
                    )

        positions: dict[tuple[int, int], list[str]] = {}
        for ref, placement in layout.components.items():
            if ref in boxes:
                positions.setdefault((placement.x, placement.y), []).append(ref)
        for position, refs_at_position in sorted(positions.items()):
            if len(refs_at_position) > 1:
                penalty = self.weights.unique_position
                hard_penalty += penalty
                violations.append(
                    PlacementViolation("UNIQUE_AUTOMATIC_COMPONENT_POSITION", ConstraintSeverity.HARD, sorted(refs_at_position), penalty, "Multiple components share one automatic placement coordinate.", {"position": position})
                )

        if fixed_layout:
            for ref in sorted(fixed_refs):
                original = fixed_layout.components.get(ref)
                current = layout.components.get(ref)
                if original and current and original != current:
                    penalty = self.weights.locked_movement
                    hard_penalty += penalty
                    violations.append(PlacementViolation("NO_LOCKED_COMPONENT_MOVEMENT", ConstraintSeverity.HARD, [ref], penalty, "Locked/manual component moved.", {"original": original.model_dump(mode="json"), "current": current.model_dump(mode="json")}))

        canvas_width = float(layout.canvas.get("width", 0) or 0)
        canvas_height = float(layout.canvas.get("height", 0) or 0)
        for ref, box in boxes.items():
            if box[0] < 0 or box[1] < 0 or (canvas_width and box[2] > canvas_width) or (canvas_height and box[3] > canvas_height):
                penalty = self.weights.outside_canvas
                hard_penalty += penalty
                violations.append(PlacementViolation("NO_COMPONENT_OUTSIDE_ALLOWED_CANVAS", ConstraintSeverity.HARD, [ref], penalty, "Component extends outside the allowed canvas.", {"box": box, "canvas": [canvas_width, canvas_height]}))

        locked_refs = {ref for ref, placement in layout.components.items() if placement.locked} | fixed_refs
        for auto_ref in sorted(set(boxes) - locked_refs):
            for locked_ref in sorted(locked_refs & set(boxes)):
                if boxes_overlap(inflate_box(boxes[auto_ref], self.minimum_clearance), inflate_box(boxes[locked_ref], self.minimum_clearance)):
                    penalty = self.weights.locked_obstacle
                    locked_obstacle_penalty += penalty
                    hard_penalty += penalty
                    violations.append(PlacementViolation("AVOID_LOCKED_OBSTACLES", ConstraintSeverity.HARD, [auto_ref, locked_ref], penalty, "Automatic component conflicts with a locked/manual obstacle."))

        wire_length, estimated_bends, wrong_facing = self._estimate_connections(circuit, library, layout)
        estimated_wire_length_penalty = wire_length * self.weights.estimated_wire_length
        estimated_bend_penalty = estimated_bends * self.weights.estimated_bend + wrong_facing * self.weights.wrong_facing
        flow_penalty = self._flow_penalty(circuit, analysis, centers)
        group_separation_penalty, controller_distance = self._group_penalties(analysis, centers)
        alignment_penalty = self._alignment_penalty(analysis, centers)
        page_metrics = _page_metrics(boxes)
        page_area_penalty = page_metrics["placement_area"] * self.weights.page_area + page_metrics["placement_height"] * self.weights.vertical_spread + page_metrics["placement_width"] * self.weights.horizontal_spread
        aspect_ratio_penalty = self._aspect_ratio_penalty(page_metrics["aspect_ratio"])

        soft_penalty = sum(
            violation.penalty for violation in violations if violation.severity == ConstraintSeverity.SOFT
        )
        soft_penalty += (
            estimated_wire_length_penalty
            + estimated_bend_penalty
            + flow_penalty
            + group_separation_penalty
            + alignment_penalty
            + page_area_penalty
            + aspect_ratio_penalty
            + controller_distance
        )
        hard_violation_count = sum(1 for violation in violations if violation.severity == ConstraintSeverity.HARD)
        total = hard_penalty + soft_penalty
        metrics = {
            **page_metrics,
            "estimated_wire_length": wire_length,
            "estimated_bends": float(estimated_bends),
            "wrong_facing_connections": float(wrong_facing),
            "controller_distance_penalty": controller_distance,
            "component_count": float(len(boxes)),
        }
        return PlacementScore(
            total=total,
            hard_violation_count=hard_violation_count,
            hard_penalty=hard_penalty,
            overlap_penalty=overlap_penalty,
            clearance_penalty=sum(v.penalty for v in violations if v.code == "MINIMUM_COMPONENT_CLEARANCE"),
            estimated_wire_length_penalty=estimated_wire_length_penalty,
            estimated_bend_penalty=estimated_bend_penalty,
            flow_penalty=flow_penalty,
            group_separation_penalty=group_separation_penalty,
            alignment_penalty=alignment_penalty,
            page_area_penalty=page_area_penalty,
            aspect_ratio_penalty=aspect_ratio_penalty,
            locked_obstacle_penalty=locked_obstacle_penalty,
            violations=violations,
            metrics=metrics,
        )

    def _estimate_connections(self, circuit: Circuit, library: ComponentLibrary, layout: Layout) -> tuple[float, int, int]:
        total = 0.0
        bends = 0
        wrong_facing = 0
        for net in sorted(circuit.nets, key=lambda item: item.name):
            endpoints: list[tuple[str, str, tuple[int, int], str]] = []
            for pin_ref in sorted(net.pins, key=lambda item: (item.component_ref, item.pin_name)):
                instance = next((component for component in circuit.components if component.ref == pin_ref.component_ref), None)
                definition = library.get(instance.component_id) if instance else None
                placement = layout.components.get(pin_ref.component_ref)
                pin = definition.resolve_pin(pin_ref.pin_name) if definition else None
                if not definition or not placement or not pin:
                    continue
                endpoints.append((pin_ref.component_ref, pin.name, absolute_pin_point(definition, placement, pin), visual_pin_side(definition, pin, placement)))
            for a, b in _manhattan_mst_edges(endpoints):
                distance = abs(a[2][0] - b[2][0]) + abs(a[2][1] - b[2][1])
                total += distance
                bends += 0 if a[2][0] == b[2][0] or a[2][1] == b[2][1] else 1
                wrong_facing += _wrong_facing(a, b)
        return total, bends, wrong_facing

    def _flow_penalty(self, circuit: Circuit, analysis: TopologyAnalysis, centers: dict[str, tuple[float, float]]) -> float:
        penalty = 0.0
        for net in sorted(circuit.nets, key=lambda item: item.name):
            refs = sorted({pin.component_ref for pin in net.pins if pin.component_ref in centers})
            for index, a in enumerate(refs):
                for b in refs[index + 1 :]:
                    rank_a = _role_rank(analysis.component_roles.get(a, ComponentRole.UNKNOWN))
                    rank_b = _role_rank(analysis.component_roles.get(b, ComponentRole.UNKNOWN))
                    if rank_a == rank_b:
                        continue
                    left_ref, right_ref = (a, b) if rank_a < rank_b else (b, a)
                    if centers[left_ref][0] > centers[right_ref][0]:
                        penalty += (centers[left_ref][0] - centers[right_ref][0] + 80) * self.weights.flow_reversal
        return penalty

    def _group_penalties(self, analysis: TopologyAnalysis, centers: dict[str, tuple[float, float]]) -> tuple[float, float]:
        group_penalty = 0.0
        controller_penalty = 0.0
        for pattern in sorted(_strong_patterns(analysis), key=lambda item: (-len(set(item.component_refs)), item.pattern_type.value, sorted(item.component_refs))):
            refs = [ref for ref in pattern.component_refs if ref in centers]
            if len(refs) < 2:
                continue
            centroid = (sum(centers[ref][0] for ref in refs) / len(refs), sum(centers[ref][1] for ref in refs) / len(refs))
            group_penalty += sum(_distance(centers[ref], centroid) for ref in refs) * self.weights.group_separation
            controllers = [ref for ref in refs if analysis.component_roles.get(ref) == ComponentRole.CONTROLLER]
            non_controllers = [ref for ref in refs if ref not in controllers]
            for controller in controllers:
                if non_controllers:
                    nearest = min(_distance(centers[controller], centers[ref]) for ref in non_controllers)
                    controller_penalty += nearest * self.weights.controller_distance
        return group_penalty, controller_penalty

    def _alignment_penalty(self, analysis: TopologyAnalysis, centers: dict[str, tuple[float, float]]) -> float:
        penalty = 0.0
        by_type: dict[PatternType, list[TopologyPattern]] = {}
        for pattern in sorted(_strong_patterns(analysis), key=lambda item: (-len(set(item.component_refs)), item.pattern_type.value, sorted(item.component_refs))):
            by_type.setdefault(pattern.pattern_type, []).append(pattern)
        for pattern_type, patterns in sorted(by_type.items(), key=lambda item: item[0].value):
            if len(patterns) < 2:
                continue
            group_centers = []
            for pattern in patterns:
                refs = [ref for ref in pattern.component_refs if ref in centers]
                if refs:
                    group_centers.append((sum(centers[ref][0] for ref in refs) / len(refs), sum(centers[ref][1] for ref in refs) / len(refs)))
            if len(group_centers) < 2:
                continue
            axis_values = [point[1] if pattern_type in {PatternType.LOW_SIDE_MOSFET_SWITCH, PatternType.RC_LOWPASS} else point[0] for point in group_centers]
            sorted_values = sorted(axis_values)
            penalty += sum(abs((b - a) - 360) for a, b in zip(sorted_values, sorted_values[1:])) * self.weights.alignment
        return penalty

    def _aspect_ratio_penalty(self, aspect_ratio: float) -> float:
        min_ratio, max_ratio = self.preferred_aspect
        if aspect_ratio < min_ratio:
            return (min_ratio - aspect_ratio) * self.weights.aspect_ratio
        if aspect_ratio > max_ratio:
            return (aspect_ratio - max_ratio) * self.weights.aspect_ratio
        return 0.0


class ConstraintPlacementOptimizer:
    def __init__(self, config: PlacementOptimizationConfig | None = None) -> None:
        self.config = config or PlacementOptimizationConfig()
        self.scorer = PlacementScorer(self.config.weights, self.config.minimum_component_clearance, (self.config.preferred_aspect_min, self.config.preferred_aspect_max))

    def optimize(
        self,
        circuit: Circuit,
        library: ComponentLibrary,
        initial_layout: Layout,
        analysis: TopologyAnalysis | None = None,
        *,
        fixed_refs: set[str] | None = None,
    ) -> PlacementOptimizationResult:
        analysis = analysis or TopologyAnalyzer().analyze(circuit, library)
        fixed_refs = fixed_refs or set()
        start = perf_counter()
        initial = initial_layout.model_copy(deep=True)
        working = initial_layout.model_copy(deep=True)
        initial_score = self.scorer.score(circuit, library, initial, analysis, fixed_refs=fixed_refs, fixed_layout=initial)
        initial_evaluation = self.evaluate_layout(circuit, library, initial, analysis, initial_score, baseline_counts=None, fixed_refs=fixed_refs, fixed_layout=initial, initial_layout=initial)
        baseline_counts = _diagnostic_counts(initial_evaluation.visual_diagnostics)
        initial_evaluation = self.evaluate_layout(circuit, library, initial, analysis, initial_score, baseline_counts=baseline_counts, fixed_refs=fixed_refs, fixed_layout=initial, initial_layout=initial)
        best_evaluation = initial_evaluation
        best_score = initial_score
        evaluations = 0
        route_validations = 0
        moves: list[dict[str, object]] = []
        candidate_reports: list[dict[str, object]] = []
        passes = 0
        budget_reached = False
        budget_reason: str | None = None
        fast_path: str | None = None

        if self.config.mode not in {"optimize", "score_only"}:
            fast_path = "optimizer_off"
            comparison = PlacementComparison(initial_score, initial_score, 0, 0, 0, (perf_counter() - start) * 1000, False, None, fast_path, [], [], initial_evaluation, initial_evaluation)
            return PlacementOptimizationResult(initial, initial, comparison)
        if self.config.mode == "score_only":
            fast_path = "score_only"
            comparison = PlacementComparison(initial_score, initial_score, 0, 0, 0, (perf_counter() - start) * 1000, False, None, fast_path, [], [], initial_evaluation, initial_evaluation)
            return PlacementOptimizationResult(initial, initial, comparison)

        units = self._move_units(analysis, circuit, working, fixed_refs)
        if not units:
            fast_path = "no_movable_components"
            comparison = PlacementComparison(initial_score, initial_score, 0, 0, 0, (perf_counter() - start) * 1000, False, None, fast_path, [], [], initial_evaluation, initial_evaluation)
            return PlacementOptimizationResult(initial, initial, comparison)
        if initial_score.hard_violation_count == 0 and not self.config.optimize_soft_constraints:
            fast_path = "hard_only_clean_layout"
            comparison = PlacementComparison(initial_score, initial_score, 0, 0, 0, (perf_counter() - start) * 1000, False, None, fast_path, [], [], initial_evaluation, initial_evaluation)
            return PlacementOptimizationResult(initial, initial, comparison)

        for pass_index in range(self.config.max_passes):
            passes = pass_index + 1
            improved = False
            route_validations_this_pass = 0
            for unit in units:
                if evaluations >= self.config.max_total_evaluations:
                    budget_reached = True
                    budget_reason = "MAX_PREFILTER_EVALUATIONS"
                    break
                if route_validations >= self.config.max_total_route_validations:
                    budget_reached = True
                    budget_reason = "MAX_ROUTE_VALIDATIONS"
                    break
                if (perf_counter() - start) * 1000 >= self.config.max_optimization_time_ms:
                    budget_reached = True
                    budget_reason = "MAX_OPTIMIZATION_TIME_MS"
                    break
                proposals, proposal_evaluations = self._prefilter_candidates(circuit, library, working, analysis, unit, best_score, initial, fixed_refs)
                evaluations += proposal_evaluations
                for proposal in proposals:
                    if route_validations >= self.config.max_total_route_validations:
                        budget_reached = True
                        budget_reason = "MAX_ROUTE_VALIDATIONS"
                        break
                    if route_validations_this_pass >= self.config.max_route_validations_per_pass:
                        budget_reached = True
                        budget_reason = "MAX_ROUTE_VALIDATIONS_PER_PASS"
                        break
                    route_start = perf_counter()
                    evaluation = self.evaluate_layout(circuit, library, proposal.layout, analysis, proposal.pre_score, baseline_counts=baseline_counts, fixed_refs=fixed_refs, fixed_layout=initial, initial_layout=initial)
                    route_validations += 1
                    route_validations_this_pass += 1
                    accepted, reason = self._accept_routed_candidate(evaluation, best_evaluation)
                    report = self._candidate_report(proposal, evaluation, accepted, reason, (perf_counter() - route_start) * 1000)
                    candidate_reports.append(report)
                    if accepted:
                        moves.append(
                            {
                                "pass": passes,
                                "candidate_id": proposal.candidate_id,
                                "unit_id": unit.unit_id,
                                "refs": list(unit.refs),
                                "transformation": proposal.transformation,
                                "score_before": best_score.total,
                                "score_after": proposal.pre_score.total,
                                "final_score_before": best_evaluation.final_score,
                                "final_score_after": evaluation.final_score,
                                "hard_before": best_score.hard_violation_count,
                                "hard_after": proposal.pre_score.hard_violation_count,
                            }
                        )
                        working = proposal.layout
                        best_score = proposal.pre_score
                        best_evaluation = evaluation
                        improved = True
                        break
                if budget_reached:
                    break
            if budget_reached or not improved:
                break

        self._resize_canvas(circuit, library, working)
        final_score = self.scorer.score(circuit, library, working, analysis, fixed_refs=fixed_refs, fixed_layout=initial)
        final_evaluation = self.evaluate_layout(circuit, library, working, analysis, final_score, baseline_counts=baseline_counts, fixed_refs=fixed_refs, fixed_layout=initial, initial_layout=initial)
        if not final_evaluation.valid:
            working = initial
            final_score = initial_score
            final_evaluation = initial_evaluation
            moves.append({"fallback": True, "reason": "LAST_KNOWN_VALID_FALLBACK"})
        comparison = PlacementComparison(
            initial_score,
            final_score,
            passes,
            evaluations,
            route_validations,
            (perf_counter() - start) * 1000,
            budget_reached,
            budget_reason,
            fast_path,
            moves,
            candidate_reports,
            initial_evaluation,
            final_evaluation,
        )
        return PlacementOptimizationResult(initial, working, comparison)

    def evaluate_layout(
        self,
        circuit: Circuit,
        library: ComponentLibrary,
        layout: Layout,
        analysis: TopologyAnalysis,
        placement_score: PlacementScore | None = None,
        *,
        baseline_counts: Counter[str] | None = None,
        fixed_refs: set[str] | None = None,
        fixed_layout: Layout | None = None,
        initial_layout: Layout | None = None,
    ) -> RoutedLayoutEvaluation:
        placement_score = placement_score or self.scorer.score(circuit, library, layout, analysis, fixed_refs=fixed_refs, fixed_layout=fixed_layout)
        rejection_reasons: list[str] = []
        if placement_score.hard_violation_count:
            rejection_reasons.append("HARD_CONSTRAINT_VIOLATION")
        routes = ManhattanRouter().route(circuit, library, layout)
        route_warnings = [warning for route in routes for warning in route.warnings]
        routing_succeeded = not route_warnings
        if not routing_succeeded:
            rejection_reasons.append("ROUTING_FAILED")
        scene = build_schematic_scene(circuit, library, layout, routes)
        visual_diagnostics, visual_metrics = _visual_drc_from_scene(scene)
        expected_visual, unexpected_visual = _split_visual_diagnostics(visual_diagnostics, baseline_counts)
        if unexpected_visual:
            rejection_reasons.append("NEW_VISUAL_DRC")
        routed_metrics = _routed_metrics(scene, visual_metrics)
        displacement, orientation_changes = _layout_displacement(initial_layout or layout, layout)
        final_score = self._final_score(placement_score, routed_metrics, len(unexpected_visual), not routing_succeeded, displacement, orientation_changes)
        scene_bounds = routed_metrics["scene_bounds"]
        assert isinstance(scene_bounds, Bounds)
        valid = not rejection_reasons
        metrics = {
            **{key: float(value) for key, value in visual_metrics.items() if isinstance(value, (int, float))},
            **{key: float(value) for key, value in routed_metrics.items() if isinstance(value, (int, float))},
            "displacement": float(displacement),
            "orientation_changes": float(orientation_changes),
        }
        return RoutedLayoutEvaluation(
            placement_score=placement_score,
            validation_level=CandidateValidationLevel.SCENE_VALIDATED,
            routing_succeeded=routing_succeeded,
            route_count=len(routes),
            total_routed_length=float(routed_metrics["total_routed_length"]),
            total_bend_count=int(routed_metrics["total_bend_count"]),
            wire_segment_count=int(routed_metrics["wire_segment_count"]),
            net_count=int(routed_metrics["net_count"]),
            max_net_length=float(routed_metrics["max_net_length"]),
            average_net_length=float(routed_metrics["average_net_length"]),
            scene_bounds=scene_bounds,
            page_area=float(routed_metrics["scene_area"]),
            aspect_ratio=float(routed_metrics["aspect_ratio"]),
            component_utilization_ratio=float(routed_metrics["component_utilization_ratio"]),
            visual_diagnostics=visual_diagnostics,
            unexpected_visual_diagnostics=unexpected_visual,
            expected_visual_diagnostics=expected_visual,
            valid=valid,
            final_score=final_score,
            rejection_reasons=rejection_reasons,
            metrics=metrics,
        )

    def _prefilter_candidates(
        self,
        circuit: Circuit,
        library: ComponentLibrary,
        layout: Layout,
        analysis: TopologyAnalysis,
        unit: MoveUnit,
        best_score: PlacementScore,
        initial_layout: Layout,
        fixed_refs: set[str],
    ) -> tuple[list[CandidateProposal], int]:
        proposals: list[CandidateProposal] = []
        evaluations = 0
        for candidate_id, candidate_layout, transformation, orientation_changes in self._candidate_layouts(circuit, library, layout, analysis, unit):
            score = self.scorer.score(circuit, library, candidate_layout, analysis, fixed_refs=fixed_refs, fixed_layout=initial_layout)
            evaluations += 1
            if score.hard_penalty > best_score.hard_penalty and score.hard_violation_count >= best_score.hard_violation_count:
                continue
            if score.hard_penalty == best_score.hard_penalty and score.hard_violation_count == best_score.hard_violation_count and not self.config.optimize_soft_constraints:
                continue
            if score.hard_penalty == best_score.hard_penalty and score.hard_violation_count == best_score.hard_violation_count and score.total >= best_score.total:
                continue
            displacement, _ = _layout_displacement(layout, candidate_layout, unit.refs)
            proposals.append(CandidateProposal(candidate_id, unit, candidate_layout, score, transformation, displacement, orientation_changes))
            if evaluations >= self.config.max_total_evaluations:
                break
        proposals.sort(key=lambda proposal: (proposal.pre_score.hard_penalty, proposal.pre_score.hard_violation_count, proposal.pre_score.total, proposal.displacement, proposal.candidate_id))
        return proposals[: self.config.max_prefilter_candidates], evaluations

    def _candidate_layouts(
        self,
        circuit: Circuit,
        library: ComponentLibrary,
        layout: Layout,
        analysis: TopologyAnalysis,
        unit: MoveUnit,
    ) -> Iterable[tuple[str, Layout, dict[str, object], int]]:
        for dx, dy in self._candidate_offsets(unit):
            if dx == 0 and dy == 0:
                continue
            candidate = self._apply_offset(layout, unit, dx, dy)
            yield (
                f"{unit.unit_id}:move:{dx}:{dy}",
                candidate,
                {"kind": "move", "offset": {"x": dx, "y": dy}},
                0,
            )
        if not self.config.enable_orientation_candidates:
            return
        locked_orientation_refs = self._strong_orientation_refs(analysis)
        for ref in unit.refs:
            placement = layout.components.get(ref)
            if not placement or placement.locked or ref in locked_orientation_refs:
                continue
            instance = next((component for component in circuit.components if component.ref == ref), None)
            definition = library.get(instance.component_id) if instance else None
            if not definition or not is_orientation_sensitive(definition):
                continue
            current = placement.rotation % 180
            target = 90 if current == 0 else 0
            candidate = layout.model_copy(deep=True)
            candidate.components[ref].rotation = target
            yield (
                f"{unit.unit_id}:rotate:{ref}:{target}",
                candidate,
                {"kind": "rotate", "component_ref": ref, "rotation": target},
                1,
            )

    def _strong_orientation_refs(self, analysis: TopologyAnalysis) -> set[str]:
        orientation_locked = {
            PatternType.VOLTAGE_DIVIDER,
            PatternType.PULL_UP,
            PatternType.PULL_DOWN,
            PatternType.DECOUPLING_CAPACITOR,
            PatternType.SERIES_GATE_RESISTOR,
            PatternType.LED_CURRENT_LIMIT,
            PatternType.LOW_SIDE_MOSFET_SWITCH,
            PatternType.RC_LOWPASS,
        }
        return {
            ref
            for pattern in _strong_patterns(analysis)
            if pattern.pattern_type in orientation_locked
            for ref in pattern.component_refs
        }

    def _accept_routed_candidate(self, evaluation: RoutedLayoutEvaluation, best: RoutedLayoutEvaluation) -> tuple[bool, str]:
        if not evaluation.valid:
            return False, ",".join(evaluation.rejection_reasons)
        if evaluation.placement_score.hard_penalty < best.placement_score.hard_penalty:
            return True, "HARD_CONSTRAINT_IMPROVED"
        if evaluation.placement_score.hard_violation_count < best.placement_score.hard_violation_count:
            return True, "HARD_CONSTRAINT_COUNT_IMPROVED"
        if evaluation.placement_score.hard_penalty > best.placement_score.hard_penalty or evaluation.placement_score.hard_violation_count > best.placement_score.hard_violation_count:
            return False, "HARD_CONSTRAINT_VIOLATION"
        if not self.config.optimize_soft_constraints:
            return False, "SOFT_OPTIMIZATION_DISABLED"
        if evaluation.final_score + self.config.min_final_score_improvement < best.final_score:
            return True, "FINAL_SCORE_IMPROVED"
        return False, "FINAL_SCORE_NOT_IMPROVED"

    def _candidate_report(
        self,
        proposal: CandidateProposal,
        evaluation: RoutedLayoutEvaluation,
        accepted: bool,
        reason: str,
        validation_time_ms: float,
    ) -> dict[str, object]:
        return {
            "candidate_id": proposal.candidate_id,
            "unit_id": proposal.unit.unit_id,
            "refs": list(proposal.unit.refs),
            "transformation": proposal.transformation,
            "pre_routing_score": proposal.pre_score.total,
            "estimated_connection_cost": proposal.pre_score.metrics.get("estimated_wire_length", 0.0),
            "routing_succeeded": evaluation.routing_succeeded,
            "actual_wire_length": evaluation.total_routed_length,
            "actual_bend_count": evaluation.total_bend_count,
            "scene_bounds": evaluation.scene_bounds.model_dump(mode="json"),
            "drc_codes": sorted(diag.code for diag in evaluation.visual_diagnostics if diag.code),
            "new_diagnostics": sorted(diag.code for diag in evaluation.unexpected_visual_diagnostics if diag.code),
            "final_score": evaluation.final_score,
            "accepted": accepted,
            "rejection_reason": None if accepted else reason,
            "validation_time_ms": validation_time_ms,
        }

    def _final_score(
        self,
        placement_score: PlacementScore,
        routed_metrics: dict[str, object],
        unexpected_visual_count: int,
        routing_failed: bool,
        displacement: float,
        orientation_changes: int,
    ) -> float:
        weights = self.config.routed_weights
        score = placement_score.hard_penalty
        if routing_failed:
            score += weights.routing_failure_penalty
        score += unexpected_visual_count * weights.visual_drc_penalty
        score += float(routed_metrics["total_routed_length"]) * weights.actual_wire_length_weight
        score += float(routed_metrics["total_bend_count"]) * weights.actual_bend_weight
        score += float(routed_metrics["max_net_length"]) * weights.max_net_length_weight
        score += float(routed_metrics["scene_area"]) * weights.page_area_weight
        aspect_ratio = float(routed_metrics["aspect_ratio"])
        if aspect_ratio < self.config.preferred_aspect_min:
            score += (self.config.preferred_aspect_min - aspect_ratio) * weights.aspect_ratio_weight
        if aspect_ratio > self.config.preferred_aspect_max:
            score += (aspect_ratio - self.config.preferred_aspect_max) * weights.aspect_ratio_weight
        score += displacement * weights.displacement_weight
        score += orientation_changes * weights.orientation_change_weight
        return score

    def _move_units(self, analysis: TopologyAnalysis, circuit: Circuit, layout: Layout, fixed_refs: set[str]) -> list[MoveUnit]:
        used: set[str] = set()
        protected_refs: set[str] = set()
        units: list[MoveUnit] = []
        strong_patterns = sorted(_strong_patterns(analysis), key=lambda item: (-len(set(item.component_refs)), item.pattern_type.value, sorted(item.component_refs)))
        membership = Counter(ref for pattern in strong_patterns for ref in set(pattern.component_refs))
        overlapping_refs = {ref for ref, count in membership.items() if count > 1}
        for pattern in strong_patterns:
            refs = tuple(ref for ref in sorted(set(pattern.component_refs)) if ref in layout.components and ref not in fixed_refs and not layout.components[ref].locked)
            if set(refs) & overlapping_refs:
                protected_refs.update(refs)
                used.update(refs)
                continue
            refs = tuple(ref for ref in refs if ref not in used)
            if len(refs) > 1:
                units.append(MoveUnit(f"group:{pattern.pattern_type.value}:{','.join(refs)}", refs))
                used.update(refs)
        for component in sorted(circuit.components, key=lambda item: item.ref):
            placement = layout.components.get(component.ref)
            if component.ref not in used and component.ref not in protected_refs and placement and not placement.locked and component.ref not in fixed_refs:
                units.append(MoveUnit(f"component:{component.ref}", (component.ref,)))
        return units

    def _candidate_offsets(self, unit: MoveUnit) -> list[tuple[int, int]]:
        offsets = [(0, 0)]
        steps = [radius * self.config.grid for radius in self.config.candidate_radii]
        for amount in steps:
            offsets.extend([(amount, 0), (-amount, 0), (0, amount), (0, -amount), (amount, amount), (amount, -amount), (-amount, amount), (-amount, -amount)])
        unique = []
        seen = set()
        for offset in offsets:
            if offset not in seen:
                seen.add(offset)
                unique.append(offset)
        return unique[: self.config.max_candidates_per_unit]

    def _apply_offset(self, layout: Layout, unit: MoveUnit, dx: int, dy: int) -> Layout:
        candidate = layout.model_copy(deep=True)
        for ref in unit.refs:
            placement = candidate.components[ref]
            placement.x = _snap(placement.x + dx, self.config.grid)
            placement.y = _snap(placement.y + dy, self.config.grid)
        return candidate

    def _is_improvement(self, score: PlacementScore, best: PlacementScore) -> bool:
        if score.hard_penalty < best.hard_penalty:
            return True
        if score.hard_penalty > best.hard_penalty:
            return False
        if score.hard_violation_count < best.hard_violation_count:
            return True
        if score.hard_violation_count > best.hard_violation_count:
            return False
        return self.config.optimize_soft_constraints and score.total + 1e-6 < best.total

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
        layout.canvas["width"] = max(int(layout.canvas.get("width", 0)), _snap(max_x + 220, self.config.grid))
        layout.canvas["height"] = max(int(layout.canvas.get("height", 0)), _snap(max_y + 220, self.config.grid))


def _visual_drc_from_scene(scene: SchematicScene) -> tuple[list[Diagnostic], dict[str, Any]]:
    diagnostics: list[Diagnostic] = []
    body_boxes: list[tuple[str, tuple[float, float, float, float]]] = []
    symbol_boxes: list[tuple[str, tuple[float, float, float, float]]] = []
    text_boxes: list[tuple[str, tuple[float, float, float, float]]] = []
    physical_text_boxes: list[tuple[str, tuple[float, float, float, float]]] = []
    pin_points: dict[str, set[tuple[int, int]]] = {}
    for element in scene.elements:
        if element.kind == "component_body" and element.component_ref:
            body_boxes.append((element.component_ref, _element_rect_box(element) or element.active_collision_bounds().as_tuple()))
        if element.kind == "component_symbol" and element.component_ref:
            symbol_boxes.append((element.component_ref, element.active_collision_bounds().as_tuple()))
        if element.kind == "pin" and element.component_ref:
            pin_points.setdefault(element.component_ref, set()).add(_pin_center(element))
        if element.text and element.metadata.get("drc_wire_text", True):
            item = (element.component_ref or "", element.text.bounds.as_tuple())
            text_boxes.append(item)
            if element.metadata.get("physical_wire_text_drc"):
                physical_text_boxes.append(item)

    wire_like = _wire_like_segments_from_scene(scene)
    component_overlaps = sum(1 for index, (_, box) in enumerate(body_boxes) for _, other in body_boxes[index + 1 :] if boxes_overlap(box, other))
    wire_symbol_overlaps = 0
    for _, _, source_ref, segment in wire_like:
        for ref, box in symbol_boxes:
            if ref == source_ref or (segment[0], segment[1]) in pin_points.get(ref, set()) or (segment[2], segment[3]) in pin_points.get(ref, set()):
                continue
            if segment_crosses_box(segment, box):
                wire_symbol_overlaps += 1
    wire_text_overlaps = sum(1 for _, _, _, segment in wire_like for ref, box in text_boxes if not _segment_touches_component_pin(segment, pin_points.get(ref, set())) and segment_crosses_box(segment, inflate_box(box, 8)))
    physical_wire_text_overlaps = sum(1 for _, kind, _, segment in wire_like if kind == "wire" for ref, box in physical_text_boxes if not _segment_touches_component_pin(segment, pin_points.get(ref, set())) and segment_crosses_box(segment, inflate_box(box, 8)))
    wire_overlaps = sum(1 for index, a in enumerate(wire_like) for b in wire_like[index + 1 :] if a[0] != b[0] and segments_collinear_overlap(a[3], b[3]))

    if component_overlaps:
        diagnostics.append(Diagnostic(severity=Severity.ERROR, code="DRC_COMPONENT_OVERLAP", message=f"{component_overlaps} component body overlaps"))
    if wire_symbol_overlaps:
        diagnostics.append(Diagnostic(severity=Severity.ERROR, code="DRC_WIRE_SYMBOL_OVERLAP", message=f"{wire_symbol_overlaps} wire/symbol overlaps"))
    if wire_overlaps:
        diagnostics.append(Diagnostic(severity=Severity.WARNING, code="DRC_WIRE_WIRE_OVERLAP", message=f"{wire_overlaps} unrelated wire overlaps"))
    if physical_wire_text_overlaps:
        diagnostics.append(Diagnostic(severity=Severity.WARNING, code="DRC_WIRE_TEXT_OVERLAP", message=f"{physical_wire_text_overlaps} physical wire/text overlaps"))

    total_wire_length = sum(abs(x1 - x2) + abs(y1 - y2) for _, _, _, (x1, y1, x2, y2) in wire_like)
    metrics = {
        "component_body_overlaps": component_overlaps,
        "wire_symbol_overlaps": wire_symbol_overlaps,
        "wire_text_overlaps": wire_text_overlaps,
        "physical_wire_text_overlaps": physical_wire_text_overlaps,
        "unrelated_wire_overlaps": wire_overlaps,
        "total_wire_length": total_wire_length,
        "physical_wire_segments": sum(1 for _, kind, _, _ in wire_like if kind == "wire"),
        "net_labels": sum(1 for element in scene.elements if element.kind == "net_label" and element.metadata.get("attachment")),
        "power_symbols": sum(1 for element in scene.elements if element.kind in {"power_symbol", "ground_symbol"}),
        "orientations": scene.metadata.get("orientations", {}),
    }
    return diagnostics, metrics


def _routed_metrics(scene: SchematicScene, visual_metrics: dict[str, Any]) -> dict[str, object]:
    wire_like = _wire_like_segments_from_scene(scene)
    lengths_by_net: dict[str, float] = {}
    segments_by_net: dict[str, list[tuple[int, int, int, int]]] = {}
    for net_name, _, _, segment in wire_like:
        length = abs(segment[0] - segment[2]) + abs(segment[1] - segment[3])
        lengths_by_net[net_name] = lengths_by_net.get(net_name, 0.0) + length
        segments_by_net.setdefault(net_name, []).append(segment)
    total_length = float(sum(lengths_by_net.values()))
    net_count = len(lengths_by_net)
    content_bounds = _content_bounds(scene)
    scene_area = max(content_bounds.width, 0) * max(content_bounds.height, 0)
    component_area = sum(element.bounds.width * element.bounds.height for element in scene.elements if element.kind == "component_body")
    diagnostic_counts = Counter(diag.code or "" for diag in _visual_drc_from_scene(scene)[0])
    return {
        "total_routed_length": total_length,
        "wire_segment_count": len(wire_like),
        "total_bend_count": _bend_count_by_net(segments_by_net),
        "net_count": net_count,
        "max_net_length": max(lengths_by_net.values(), default=0.0),
        "average_net_length": total_length / net_count if net_count else 0.0,
        "scene_bounds": content_bounds,
        "scene_width": content_bounds.width,
        "scene_height": content_bounds.height,
        "scene_area": scene_area,
        "aspect_ratio": content_bounds.width / content_bounds.height if content_bounds.height else 1.0,
        "component_utilization_ratio": component_area / scene_area if scene_area else 0.0,
        "visual_diagnostic_count": sum(diagnostic_counts.values()),
        **{f"visual_diagnostic_{code}": count for code, count in sorted(diagnostic_counts.items()) if code},
        **{key: value for key, value in visual_metrics.items() if isinstance(value, (int, float))},
    }


def _wire_like_segments_from_scene(scene: SchematicScene) -> list[tuple[str, str, str, tuple[int, int, int, int]]]:
    segments: list[tuple[str, str, str, tuple[int, int, int, int]]] = []
    for element in scene.elements:
        if element.kind not in {"wire", "wire_stub"}:
            continue
        segment = _line_segment_from_element(element)
        if not segment:
            continue
        segments.append((element.net_name or "", str(element.metadata.get("wire_kind", "wire")), str(element.metadata.get("source_ref", "")), segment))
    return segments


def _line_segment_from_element(element: Any) -> tuple[int, int, int, int] | None:
    primitive = next((primitive for primitive in element.primitives if primitive.kind == "line"), None)
    if primitive:
        geometry = primitive.geometry
        return (round(float(geometry["x1"])), round(float(geometry["y1"])), round(float(geometry["x2"])), round(float(geometry["y2"])))
    segment = element.metadata.get("segment")
    if segment and len(segment) == 4:
        return (int(segment[0]), int(segment[1]), int(segment[2]), int(segment[3]))
    return None


def _element_rect_box(element: Any) -> tuple[float, float, float, float] | None:
    primitive = next((primitive for primitive in element.primitives if primitive.kind == "rect"), None)
    if not primitive:
        return None
    geometry = primitive.geometry
    x = float(geometry["x"])
    y = float(geometry["y"])
    return (x, y, x + float(geometry["width"]), y + float(geometry["height"]))


def _pin_center(element: Any) -> tuple[int, int]:
    primitive = element.primitives[0].geometry if element.primitives else {}
    if "cx" in primitive and "cy" in primitive:
        return (round(float(primitive["cx"])), round(float(primitive["cy"])))
    return (round((element.bounds.min_x + element.bounds.max_x) / 2), round((element.bounds.min_y + element.bounds.max_y) / 2))


def _segment_touches_component_pin(segment: tuple[int, int, int, int], pins: set[tuple[int, int]]) -> bool:
    return (segment[0], segment[1]) in pins or (segment[2], segment[3]) in pins


def _content_bounds(scene: SchematicScene) -> Bounds:
    visible_bounds = [element.bounds for element in scene.elements if element.visible]
    if not visible_bounds:
        return scene.canvas_bounds
    return Bounds(
        min_x=min(bounds.min_x for bounds in visible_bounds),
        min_y=min(bounds.min_y for bounds in visible_bounds),
        max_x=max(bounds.max_x for bounds in visible_bounds),
        max_y=max(bounds.max_y for bounds in visible_bounds),
    )


def _bend_count_by_net(segments_by_net: dict[str, list[tuple[int, int, int, int]]]) -> int:
    bends = 0
    for segments in segments_by_net.values():
        previous_orientation: str | None = None
        previous_end: tuple[int, int] | None = None
        for segment in segments:
            orientation = "vertical" if segment[0] == segment[2] else "horizontal" if segment[1] == segment[3] else "diagonal"
            start = (segment[0], segment[1])
            end = (segment[2], segment[3])
            if previous_orientation and previous_end == start and previous_orientation != orientation:
                bends += 1
            previous_orientation = orientation
            previous_end = end
    return bends


def _diagnostic_counts(diagnostics: list[Diagnostic]) -> Counter[str]:
    return Counter(diag.code or "" for diag in diagnostics if diag.code)


def _split_visual_diagnostics(diagnostics: list[Diagnostic], baseline_counts: Counter[str] | None) -> tuple[list[Diagnostic], list[Diagnostic]]:
    if baseline_counts is None:
        return diagnostics, []
    used: Counter[str] = Counter()
    expected: list[Diagnostic] = []
    unexpected: list[Diagnostic] = []
    for diag in diagnostics:
        code = diag.code or ""
        if code and used[code] < baseline_counts.get(code, 0):
            expected.append(diag)
            used[code] += 1
        else:
            unexpected.append(diag)
    return expected, unexpected


def _layout_displacement(initial: Layout, current: Layout, refs: Iterable[str] | None = None) -> tuple[float, int]:
    refs_to_check = sorted(set(refs or set(initial.components) | set(current.components)))
    displacement = 0.0
    orientation_changes = 0
    for ref in refs_to_check:
        original = initial.components.get(ref)
        moved = current.components.get(ref)
        if not original or not moved:
            continue
        displacement += abs(original.x - moved.x) + abs(original.y - moved.y)
        if original.rotation % 360 != moved.rotation % 360:
            orientation_changes += 1
    return displacement, orientation_changes


def _component_boxes(circuit: Circuit, library: ComponentLibrary, layout: Layout) -> dict[str, tuple[float, float, float, float]]:
    boxes = {}
    for component in circuit.components:
        definition = library.get(component.component_id)
        placement = layout.components.get(component.ref)
        if definition and placement:
            boxes[component.ref] = component_body_box(definition, placement)
    return boxes


def _page_metrics(boxes: dict[str, tuple[float, float, float, float]]) -> dict[str, float]:
    if not boxes:
        return {"placement_width": 0.0, "placement_height": 0.0, "placement_area": 0.0, "occupied_body_area": 0.0, "utilization_ratio": 0.0, "aspect_ratio": 1.0}
    min_x = min(box[0] for box in boxes.values())
    min_y = min(box[1] for box in boxes.values())
    max_x = max(box[2] for box in boxes.values())
    max_y = max(box[3] for box in boxes.values())
    width = max_x - min_x
    height = max_y - min_y
    area = width * height
    occupied = sum((box[2] - box[0]) * (box[3] - box[1]) for box in boxes.values())
    return {
        "placement_width": width,
        "placement_height": height,
        "placement_area": area,
        "occupied_body_area": occupied,
        "utilization_ratio": occupied / area if area else 0.0,
        "aspect_ratio": width / height if height else 1.0,
    }


def _manhattan_mst_edges(endpoints: list[tuple[str, str, tuple[int, int], str]]) -> list[tuple[tuple[str, str, tuple[int, int], str], tuple[str, str, tuple[int, int], str]]]:
    if len(endpoints) < 2:
        return []
    endpoints = sorted(endpoints, key=lambda item: (item[0], item[1], item[2]))
    connected = {0}
    remaining = set(range(1, len(endpoints)))
    edges = []
    while remaining:
        best = min(
            (
                (abs(endpoints[a][2][0] - endpoints[b][2][0]) + abs(endpoints[a][2][1] - endpoints[b][2][1]), a, b)
                for a in connected
                for b in remaining
            ),
            key=lambda item: (item[0], endpoints[item[1]][0], endpoints[item[2]][0], item[1], item[2]),
        )
        _, a, b = best
        connected.add(b)
        remaining.remove(b)
        edges.append((endpoints[a], endpoints[b]))
    return edges


def _wrong_facing(a: tuple[str, str, tuple[int, int], str], b: tuple[str, str, tuple[int, int], str]) -> int:
    dx = b[2][0] - a[2][0]
    dy = b[2][1] - a[2][1]
    return int(_pin_faces_away(a[3], dx, dy) or _pin_faces_away(b[3], -dx, -dy))


def _pin_faces_away(side: str, dx: int, dy: int) -> bool:
    if side == "left":
        return dx > 0
    if side == "right":
        return dx < 0
    if side == "top":
        return dy > 0
    if side == "bottom":
        return dy < 0
    return False


def _role_rank(role: ComponentRole) -> int:
    return {
        ComponentRole.SOURCE: 0,
        ComponentRole.CHARGER: 1,
        ComponentRole.REGULATOR: 1,
        ComponentRole.STORAGE: 2,
        ComponentRole.CONTROLLER: 3,
        ComponentRole.SWITCH: 4,
        ComponentRole.LOAD: 5,
    }.get(role, 3)


def _strong_patterns(analysis: TopologyAnalysis) -> list[TopologyPattern]:
    return [pattern for pattern in analysis.patterns if pattern.confidence >= MIN_PLACEMENT_PATTERN_CONFIDENCE]


def _box_center(box: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _box_clearance(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    dx = max(a[0] - b[2], b[0] - a[2], 0)
    dy = max(a[1] - b[3], b[1] - a[3], 0)
    return (dx * dx + dy * dy) ** 0.5


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _snap(value: int, grid: int) -> int:
    return round(value / grid) * grid
