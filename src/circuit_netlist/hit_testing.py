from __future__ import annotations

from math import hypot

from .scene import Bounds, Point, SceneElement, RenderPrimitive, SchematicScene


def hit_test_point(scene: SchematicScene, x: float, y: float, tolerance: float = 4) -> SceneElement | None:
    hits = hit_test_all(scene, x, y, tolerance)
    return hits[0] if hits else None


def hit_test_all(scene: SchematicScene, x: float, y: float, tolerance: float = 4) -> list[SceneElement]:
    point = Point(x=x, y=y)
    candidates = [
        element
        for element in scene.elements
        if element.selectable and element.visible and element.hit_test_enabled and _element_contains_point(element, point, tolerance)
    ]
    return sorted(candidates, key=lambda element: (element.layer == "components", element.z_index), reverse=True)


def hit_test_bounds(scene: SchematicScene, bounds: Bounds) -> list[SceneElement]:
    return [
        element
        for element in scene.elements
        if element.selectable and element.visible and element.active_hit_bounds().intersects(bounds)
    ]


def elements_for_component(scene: SchematicScene, ref: str) -> list[SceneElement]:
    return [element for element in scene.elements if element.component_ref == ref]


def elements_for_net(scene: SchematicScene, net_name: str) -> list[SceneElement]:
    return [element for element in scene.elements if element.net_name == net_name]


def _element_contains_point(element: SceneElement, point: Point, tolerance: float) -> bool:
    if element.kind in {"wire", "wire_stub"}:
        return any(_primitive_line_distance(primitive, point) <= tolerance for primitive in element.primitives if primitive.kind == "line")
    if element.kind in {"component_body", "pin", "junction", "power_symbol", "ground_symbol"}:
        if any(_primitive_contains_point(primitive, point, tolerance) for primitive in element.primitives):
            return True
    return element.active_hit_bounds().contains_point(point, tolerance)


def _primitive_contains_point(primitive: RenderPrimitive, point: Point, tolerance: float) -> bool:
    geometry = primitive.geometry
    if primitive.kind == "rect":
        return (
            float(geometry["x"]) - tolerance <= point.x <= float(geometry["x"]) + float(geometry["width"]) + tolerance
            and float(geometry["y"]) - tolerance <= point.y <= float(geometry["y"]) + float(geometry["height"]) + tolerance
        )
    if primitive.kind == "circle":
        return hypot(point.x - float(geometry["cx"]), point.y - float(geometry["cy"])) <= float(geometry["r"]) + tolerance
    if primitive.kind == "line":
        return _primitive_line_distance(primitive, point) <= tolerance
    return False


def _primitive_line_distance(primitive: RenderPrimitive, point: Point) -> float:
    geometry = primitive.geometry
    x1 = float(geometry["x1"])
    y1 = float(geometry["y1"])
    x2 = float(geometry["x2"])
    y2 = float(geometry["y2"])
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return hypot(point.x - x1, point.y - y1)
    t = max(0.0, min(1.0, ((point.x - x1) * dx + (point.y - y1) * dy) / (dx * dx + dy * dy)))
    closest_x = x1 + t * dx
    closest_y = y1 + t * dy
    return hypot(point.x - closest_x, point.y - closest_y)
