from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


SCENE_VERSION = "1.0"


class Point(BaseModel):
    x: float
    y: float

    @classmethod
    def from_pair(cls, pair: tuple[float, float]) -> "Point":
        return cls(x=pair[0], y=pair[1])


class Bounds(BaseModel):
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @classmethod
    def from_tuple(cls, box: tuple[float, float, float, float]) -> "Bounds":
        return cls(min_x=box[0], min_y=box[1], max_x=box[2], max_y=box[3])

    @classmethod
    def from_points(cls, points: list[Point]) -> "Bounds":
        if not points:
            return cls(min_x=0, min_y=0, max_x=0, max_y=0)
        return cls(
            min_x=min(point.x for point in points),
            min_y=min(point.y for point in points),
            max_x=max(point.x for point in points),
            max_y=max(point.y for point in points),
        )

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.min_x, self.min_y, self.max_x, self.max_y)

    def expanded(self, padding: float) -> "Bounds":
        return Bounds(
            min_x=self.min_x - padding,
            min_y=self.min_y - padding,
            max_x=self.max_x + padding,
            max_y=self.max_y + padding,
        )

    def intersects(self, other: "Bounds") -> bool:
        return max(self.min_x, other.min_x) < min(self.max_x, other.max_x) and max(self.min_y, other.min_y) < min(self.max_y, other.max_y)

    def contains_point(self, point: Point, tolerance: float = 0) -> bool:
        return (
            self.min_x - tolerance <= point.x <= self.max_x + tolerance
            and self.min_y - tolerance <= point.y <= self.max_y + tolerance
        )


class LineSegment(BaseModel):
    start: Point
    end: Point

    @classmethod
    def from_tuple(cls, segment: tuple[int, int, int, int] | tuple[float, float, float, float]) -> "LineSegment":
        return cls(start=Point(x=segment[0], y=segment[1]), end=Point(x=segment[2], y=segment[3]))

    @property
    def bounds(self) -> Bounds:
        return Bounds.from_points([self.start, self.end])

    @property
    def orientation(self) -> Literal["horizontal", "vertical", "diagonal", "point"]:
        if self.start.x == self.end.x and self.start.y == self.end.y:
            return "point"
        if self.start.y == self.end.y:
            return "horizontal"
        if self.start.x == self.end.x:
            return "vertical"
        return "diagonal"

    @property
    def manhattan_length(self) -> float:
        return abs(self.start.x - self.end.x) + abs(self.start.y - self.end.y)

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (round(self.start.x), round(self.start.y), round(self.end.x), round(self.end.y))


class Polyline(BaseModel):
    points: list[Point]

    @classmethod
    def from_segments(cls, segments: list[tuple[int, int, int, int]]) -> "Polyline":
        points: list[Point] = []
        for x1, y1, x2, y2 in segments:
            if not points:
                points.append(Point(x=x1, y=y1))
            elif points[-1].x != x1 or points[-1].y != y1:
                points.append(Point(x=x1, y=y1))
            points.append(Point(x=x2, y=y2))
        return cls(points=points)

    @property
    def segments(self) -> list[LineSegment]:
        return [LineSegment(start=start, end=end) for start, end in zip(self.points, self.points[1:])]

    @property
    def bounds(self) -> Bounds:
        return Bounds.from_points(self.points)


class TextGeometry(BaseModel):
    text: str
    origin: Point
    anchor: str = "start"
    bounds: Bounds


class RenderPrimitive(BaseModel):
    id: str | None = None
    kind: str
    geometry: dict[str, Any] = Field(default_factory=dict)
    style_class: str | None = None
    stroke: str | None = None
    stroke_width: float | None = None
    fill: str | None = None
    line_cap: str | None = None
    line_join: str | None = None
    dash_pattern: str | None = None
    text_style: dict[str, Any] = Field(default_factory=dict)
    visible: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class SceneElement(BaseModel):
    id: str
    kind: str
    layer: str
    z_index: int = 0
    owner_id: str | None = None
    parent_id: str | None = None
    component_ref: str | None = None
    component_id: str | None = None
    pin_name: str | None = None
    pin_number: str | None = None
    net_name: str | None = None
    bounds: Bounds
    collision_bounds: Bounds | None = None
    hit_bounds: Bounds | None = None
    primitives: list[RenderPrimitive] = Field(default_factory=list)
    text: TextGeometry | None = None
    selectable: bool = False
    visible: bool = True
    drc_enabled: bool = True
    hit_test_enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    def active_collision_bounds(self) -> Bounds:
        return self.collision_bounds or self.bounds

    def active_hit_bounds(self) -> Bounds:
        return self.hit_bounds or self.bounds


class SchematicScene(BaseModel):
    scene_version: str = SCENE_VERSION
    circuit_name: str
    canvas_bounds: Bounds
    elements: list[SceneElement] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def elements_by_kind(self, kind: str) -> list[SceneElement]:
        return [element for element in self.elements if element.kind == kind]

    def elements_by_layer(self, layer: str) -> list[SceneElement]:
        return [element for element in self.elements if element.layer == layer and element.visible]

    def first(self, element_id: str) -> SceneElement | None:
        return next((element for element in self.elements if element.id == element_id), None)
