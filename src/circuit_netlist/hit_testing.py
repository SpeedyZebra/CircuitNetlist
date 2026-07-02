from __future__ import annotations

from .scene import Bounds, Point, SceneElement, SchematicScene


def hit_test_point(scene: SchematicScene, x: float, y: float, tolerance: float = 4) -> SceneElement | None:
    point = Point(x=x, y=y)
    candidates = [
        element
        for element in scene.elements
        if element.selectable and element.visible and element.active_hit_bounds().contains_point(point, tolerance)
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda element: (element.layer == "components", element.z_index), reverse=True)[0]


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
