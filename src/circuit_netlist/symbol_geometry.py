from __future__ import annotations

from .geometry import component_size, local_pin_anchor
from .models import ComponentDefinition, Placement
from .scene import RenderPrimitive


MOSFET_SYMBOL_STYLE = "compact_no_bulk"


def component_symbol_primitives(definition: ComponentDefinition, placement: Placement) -> list[RenderPrimitive]:
    renderer = definition.body.renderer
    if renderer in {"functional_block", "battery", "solar_panel"}:
        return []
    if renderer == "dc_source":
        return dc_source_primitives(placement)
    if renderer == "dip_ic":
        return dip_ic_primitives(definition, placement)
    if renderer == "resistor":
        return resistor_primitives(placement)
    if renderer in {"capacitor", "polarized_capacitor"}:
        return capacitor_primitives(definition, placement)
    if renderer == "inductor":
        return inductor_primitives(placement)
    if renderer == "op_amp":
        return op_amp_primitives(placement)
    if renderer == "timer_555":
        return timer_555_primitives(definition, placement)
    if renderer == "led":
        return led_primitives(placement)
    if renderer in {"nmos", "pmos"}:
        return mosfet_primitives(definition, placement)
    if renderer == "ground":
        return ground_primitives(placement)
    if renderer == "test_point":
        return test_point_primitives(placement)
    return []


def pin_contact_primitives(definition: ComponentDefinition, placement: Placement) -> list[RenderPrimitive]:
    primitives: list[RenderPrimitive] = []
    for pin in definition.pins:
        x, y = local_pin_anchor(definition, pin, placement)
        primitives.append(circle(placement, x, y, 5, "pin-contact"))
    return primitives


def dc_source_primitives(placement: Placement) -> list[RenderPrimitive]:
    return [
        line(placement, 50, 0, 50, 24, "symbol"),
        line(placement, 50, 96, 50, 120, "symbol"),
        circle(placement, 50, 60, 36, "symbol"),
        line(placement, 38, 42, 62, 42, "symbol"),
        line(placement, 50, 30, 50, 54, "symbol"),
        line(placement, 38, 82, 62, 82, "symbol"),
        circle(placement, 50, 0, 5, "pin-contact"),
        circle(placement, 50, 120, 5, "pin-contact"),
    ]


def dip_ic_primitives(definition: ComponentDefinition, placement: Placement) -> list[RenderPrimitive]:
    w, _ = component_size(definition, placement)
    return [
        path(placement, f"M {w / 2 - 18} 0 A 18 18 0 0 0 {w / 2 + 18} 0", "notch"),
        circle(placement, 14, 24, 4, "pin-one"),
    ]


def resistor_primitives(placement: Placement) -> list[RenderPrimitive]:
    if placement.rotation % 180 == 90:
        return [polyline(placement, [(40, 0), (40, 25), (25, 32), (55, 46), (25, 60), (55, 74), (25, 88), (55, 102), (40, 110), (40, 140)], "symbol")]
    return [polyline(placement, [(0, 40), (25, 40), (32, 25), (46, 55), (60, 25), (74, 55), (88, 25), (102, 55), (110, 40), (140, 40)], "symbol")]


def capacitor_primitives(definition: ComponentDefinition, placement: Placement) -> list[RenderPrimitive]:
    if placement.rotation % 180 == 90:
        primitives = [
            line(placement, 40, 0, 40, 55, "symbol"),
            line(placement, 18, 65, 62, 65, "symbol"),
            line(placement, 18, 85, 62, 85, "symbol"),
            line(placement, 40, 85, 40, 140, "symbol"),
        ]
    else:
        primitives = [
            line(placement, 0, 40, 55, 40, "symbol"),
            line(placement, 65, 18, 65, 62, "symbol"),
            line(placement, 85, 18, 85, 62, "symbol"),
            line(placement, 85, 40, 140, 40, "symbol"),
        ]
    if definition.body.renderer == "polarized_capacitor":
        primitives.append(text(placement, 48, 20, "+", "polarity"))
    return primitives


def inductor_primitives(placement: Placement) -> list[RenderPrimitive]:
    return [path(placement, "M 0 40 H 25 C 25 20 45 20 45 40 C 45 20 65 20 65 40 C 65 20 85 20 85 40 C 85 20 105 20 105 40 H 140", "symbol")]


def op_amp_primitives(placement: Placement) -> list[RenderPrimitive]:
    return [
        polygon(placement, [(20, 20), (20, 100), (140, 60)], "symbol"),
        line(placement, 0, 40, 20, 40, "symbol"),
        line(placement, 0, 80, 20, 80, "symbol"),
        line(placement, 140, 60, 160, 60, "symbol"),
        line(placement, 80, 0, 80, 40, "symbol"),
        line(placement, 80, 80, 80, 120, "symbol"),
        text(placement, 28, 44, "-", "polarity"),
        text(placement, 28, 84, "+", "polarity"),
        circle(placement, 0, 40, 5, "pin-contact"),
        circle(placement, 0, 80, 5, "pin-contact"),
        circle(placement, 160, 60, 5, "pin-contact"),
        circle(placement, 80, 0, 5, "pin-contact"),
        circle(placement, 80, 120, 5, "pin-contact"),
    ]


def timer_555_primitives(definition: ComponentDefinition, placement: Placement) -> list[RenderPrimitive]:
    w, h = component_size(definition, placement)
    return [
        path(placement, f"M {w / 2 - 18} 0 A 18 18 0 0 0 {w / 2 + 18} 0", "notch"),
        circle(placement, 144, 206, 4, "pin-one"),
        text(placement, w / 2, h / 2 - 8, "555", "timer-title", anchor="middle"),
        text(placement, w / 2, h / 2 + 18, "Astable", "timer-subtitle", anchor="middle"),
    ]


def led_primitives(placement: Placement) -> list[RenderPrimitive]:
    return [
        line(placement, 0, 40, 50, 40, "symbol"),
        polygon(placement, [(50, 15), (50, 65), (90, 40)], "symbol-fill"),
        line(placement, 92, 15, 92, 65, "symbol"),
        line(placement, 92, 40, 140, 40, "symbol"),
        path(placement, "M 105 10 l 18 -18 M 114 10 l 18 -18", "symbol"),
        circle(placement, 0, 40, 5, "pin-contact"),
        circle(placement, 140, 40, 5, "pin-contact"),
    ]


def mosfet_primitives(definition: ComponentDefinition, placement: Placement) -> list[RenderPrimitive]:
    is_pmos = definition.body.renderer == "pmos"
    mode = str(definition.metadata.get("mosfet_mode", "enhancement"))
    primitives = [
        line(placement, 0, 40, 42, 40, "symbol mosfet-gate-lead"),
        line(placement, 52, 28, 52, 92, "symbol mosfet-gate"),
        line(placement, 80, 0, 80, 28, "symbol mosfet-top-lead"),
        line(placement, 80, 92, 80, 120, "symbol mosfet-bottom-lead"),
    ]
    if mode == "depletion":
        primitives.append(line(placement, 80, 28, 80, 92, "symbol mosfet-channel depletion-channel"))
    else:
        primitives.extend(
            [
                line(placement, 80, 28, 80, 44, "symbol mosfet-channel enhancement-channel"),
                line(placement, 80, 54, 80, 66, "symbol mosfet-channel enhancement-channel"),
                line(placement, 80, 76, 80, 92, "symbol mosfet-channel enhancement-channel"),
            ]
        )
    primitives.extend(
        [
            line(placement, 66, 32, 94, 32, "symbol mosfet-top-terminal"),
            line(placement, 66, 88, 94, 88, "symbol mosfet-bottom-terminal"),
        ]
    )
    if is_pmos:
        primitives.append(path(placement, "M 78 36 L 66 36 M 71 31 L 66 36 L 71 41", "symbol mosfet-arrow pmos-arrow"))
    else:
        primitives.append(path(placement, "M 64 84 L 76 84 M 71 79 L 76 84 L 71 89", "symbol mosfet-arrow nmos-arrow"))
    if definition.metadata.get("show_body_diode") is True:
        if is_pmos:
            primitives.extend(
                [
                    path(placement, "M 110 92 L 110 28", "symbol mosfet-body-diode pmos-body-diode"),
                    polygon(placement, [(110, 44), (102, 60), (118, 60)], "symbol-fill mosfet-diode-triangle"),
                    line(placement, 102, 44, 118, 44, "symbol mosfet-diode-bar"),
                ]
            )
        else:
            primitives.extend(
                [
                    path(placement, "M 110 28 L 110 92", "symbol mosfet-body-diode nmos-body-diode"),
                    polygon(placement, [(110, 76), (102, 60), (118, 60)], "symbol-fill mosfet-diode-triangle"),
                    line(placement, 102, 76, 118, 76, "symbol mosfet-diode-bar"),
                ]
            )
    if any(pin.name == "B" for pin in definition.pins):
        primitives.extend([line(placement, 140, 60, 96, 60, "symbol mosfet-bulk-lead"), circle(placement, 140, 60, 5, "pin-contact")])
    primitives.extend([circle(placement, 0, 40, 5, "pin-contact"), circle(placement, 80, 0, 5, "pin-contact"), circle(placement, 80, 120, 5, "pin-contact")])
    for primitive in primitives:
        primitive.metadata["symbol_style"] = MOSFET_SYMBOL_STYLE
        primitive.metadata["mosfet_mode"] = mode
    return primitives


def ground_primitives(placement: Placement) -> list[RenderPrimitive]:
    return [
        line(placement, 60, 0, 60, 30, "symbol"),
        line(placement, 25, 30, 95, 30, "symbol"),
        line(placement, 35, 45, 85, 45, "symbol"),
        line(placement, 45, 60, 75, 60, "symbol"),
    ]


def test_point_primitives(placement: Placement) -> list[RenderPrimitive]:
    return [circle(placement, 50, 35, 16, "symbol"), line(placement, 0, 35, 34, 35, "symbol")]


def line(placement: Placement, x1: float, y1: float, x2: float, y2: float, css_class: str) -> RenderPrimitive:
    return RenderPrimitive(kind="line", style_class=css_class, geometry={"x1": placement.x + x1, "y1": placement.y + y1, "x2": placement.x + x2, "y2": placement.y + y2})


def circle(placement: Placement, cx: float, cy: float, r: float, css_class: str) -> RenderPrimitive:
    return RenderPrimitive(kind="circle", style_class=css_class, geometry={"cx": placement.x + cx, "cy": placement.y + cy, "r": r})


def rect(placement: Placement, x: float, y: float, width: float, height: float, css_class: str, rx: float | None = None) -> RenderPrimitive:
    geometry = {"x": placement.x + x, "y": placement.y + y, "width": width, "height": height}
    if rx is not None:
        geometry["rx"] = rx
    return RenderPrimitive(kind="rect", style_class=css_class, geometry=geometry)


def polyline(placement: Placement, points: list[tuple[float, float]], css_class: str) -> RenderPrimitive:
    return RenderPrimitive(kind="polyline", style_class=css_class, geometry={"points": [(placement.x + x, placement.y + y) for x, y in points]})


def polygon(placement: Placement, points: list[tuple[float, float]], css_class: str) -> RenderPrimitive:
    return RenderPrimitive(kind="polygon", style_class=css_class, geometry={"points": [(placement.x + x, placement.y + y) for x, y in points]})


def path(placement: Placement, d: str, css_class: str) -> RenderPrimitive:
    return RenderPrimitive(kind="path", style_class=css_class, geometry={"d": d, "translate": (placement.x, placement.y)})


def text(placement: Placement, x: float, y: float, value: str, css_class: str, anchor: str = "start") -> RenderPrimitive:
    return RenderPrimitive(kind="text", style_class=css_class, geometry={"x": placement.x + x, "y": placement.y + y, "text": value, "anchor": anchor})
