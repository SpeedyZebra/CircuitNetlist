from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from time import perf_counter
from typing import Iterable

from .component_library import ComponentLibrary
from .geometry import absolute_pin_point, boxes_overlap, component_body_box, component_size, inflate_box, visual_pin_side
from .models import Circuit, Layout, Placement
from .topology import ComponentRole, MIN_PLACEMENT_PATTERN_CONFIDENCE, PatternType, TopologyAnalysis, TopologyAnalyzer, TopologyPattern


class ConstraintSeverity(str, Enum):
    HARD = "hard"
    SOFT = "soft"


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
class PlacementOptimizationConfig:
    mode: str = "optimize"
    grid: int = 40
    max_passes: int = 4
    max_candidates_per_unit: int = 25
    max_total_evaluations: int = 600
    candidate_radii: tuple[int, ...] = (2, 4, 6)
    minimum_component_clearance: int = 24
    preferred_aspect_min: float = 1.0
    preferred_aspect_max: float = 1.8
    optimize_soft_constraints: bool = False
    weights: PlacementWeights = DEFAULT_PLACEMENT_WEIGHTS


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
class PlacementComparison:
    initial_score: PlacementScore
    optimized_score: PlacementScore
    passes: int
    candidate_evaluations: int
    elapsed_ms: float
    budget_reached: bool
    moves: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return {
            "initial_score": self.initial_score.to_dict(),
            "optimized_score": self.optimized_score.to_dict(),
            "passes": self.passes,
            "candidate_evaluations": self.candidate_evaluations,
            "elapsed_ms": self.elapsed_ms,
            "budget_reached": self.budget_reached,
            "moves": self.moves,
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
        for pattern in _strong_patterns(analysis):
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
        for pattern in _strong_patterns(analysis):
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
        best_score = initial_score
        evaluations = 0
        moves: list[dict[str, object]] = []
        passes = 0
        budget_reached = False

        if self.config.mode not in {"optimize", "score_only"}:
            comparison = PlacementComparison(initial_score, initial_score, 0, 0, (perf_counter() - start) * 1000, False, [])
            return PlacementOptimizationResult(initial, initial, comparison)
        if self.config.mode == "score_only":
            comparison = PlacementComparison(initial_score, initial_score, 0, 0, (perf_counter() - start) * 1000, False, [])
            return PlacementOptimizationResult(initial, initial, comparison)

        units = self._move_units(analysis, circuit, working, fixed_refs)
        for pass_index in range(self.config.max_passes):
            passes = pass_index + 1
            improved = False
            for unit in units:
                if evaluations >= self.config.max_total_evaluations:
                    budget_reached = True
                    break
                candidate_score = best_score
                candidate_layout = working
                candidate_offset = (0, 0)
                for dx, dy in self._candidate_offsets(unit):
                    if dx == 0 and dy == 0:
                        continue
                    candidate = self._apply_offset(working, unit, dx, dy)
                    score = self.scorer.score(circuit, library, candidate, analysis, fixed_refs=fixed_refs, fixed_layout=initial)
                    evaluations += 1
                    if self._is_improvement(score, candidate_score):
                        candidate_score = score
                        candidate_layout = candidate
                        candidate_offset = (dx, dy)
                    if evaluations >= self.config.max_total_evaluations:
                        budget_reached = True
                        break
                if candidate_layout is not working:
                    moves.append(
                        {
                            "pass": passes,
                            "unit_id": unit.unit_id,
                            "refs": list(unit.refs),
                            "offset": {"x": candidate_offset[0], "y": candidate_offset[1]},
                            "score_before": best_score.total,
                            "score_after": candidate_score.total,
                            "hard_before": best_score.hard_violation_count,
                            "hard_after": candidate_score.hard_violation_count,
                        }
                    )
                    working = candidate_layout
                    best_score = candidate_score
                    improved = True
                if budget_reached:
                    break
            if budget_reached or not improved:
                break

        self._resize_canvas(circuit, library, working)
        final_score = self.scorer.score(circuit, library, working, analysis, fixed_refs=fixed_refs, fixed_layout=initial)
        if final_score.total > initial_score.total and final_score.hard_violation_count >= initial_score.hard_violation_count:
            working = initial
            final_score = initial_score
            moves.append({"fallback": True, "reason": "optimized score was worse than initial score"})
        comparison = PlacementComparison(initial_score, final_score, passes, evaluations, (perf_counter() - start) * 1000, budget_reached, moves)
        return PlacementOptimizationResult(initial, working, comparison)

    def _move_units(self, analysis: TopologyAnalysis, circuit: Circuit, layout: Layout, fixed_refs: set[str]) -> list[MoveUnit]:
        used: set[str] = set()
        units: list[MoveUnit] = []
        for pattern in _strong_patterns(analysis):
            refs = tuple(ref for ref in sorted(set(pattern.component_refs)) if ref in layout.components and ref not in fixed_refs and not layout.components[ref].locked)
            refs = tuple(ref for ref in refs if ref not in used)
            if len(refs) > 1:
                units.append(MoveUnit(f"group:{pattern.pattern_type.value}:{','.join(refs)}", refs))
                used.update(refs)
        for component in sorted(circuit.components, key=lambda item: item.ref):
            placement = layout.components.get(component.ref)
            if component.ref not in used and placement and not placement.locked and component.ref not in fixed_refs:
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
