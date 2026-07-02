from __future__ import annotations

from enum import Enum
import re
from typing import Any

from pydantic import BaseModel, Field

from .component_library import ComponentLibrary
from .models import Circuit, ComponentDefinition, ComponentInstance, ElectricalType, Net, PinDefinition


class ComponentRole(str, Enum):
    SOURCE = "source"
    STORAGE = "storage"
    CHARGER = "charger"
    REGULATOR = "regulator"
    CONTROLLER = "controller"
    SENSOR = "sensor"
    PASSIVE = "passive"
    SWITCH = "switch"
    LOAD = "load"
    PROTECTION = "protection"
    CONNECTOR = "connector"
    UNKNOWN = "unknown"


class PatternType(str, Enum):
    VOLTAGE_DIVIDER = "voltage_divider"
    PULL_UP = "pull_up"
    PULL_DOWN = "pull_down"
    DECOUPLING_CAPACITOR = "decoupling_capacitor"
    SERIES_GATE_RESISTOR = "series_gate_resistor"
    LED_CURRENT_LIMIT = "led_current_limit"
    LOW_SIDE_MOSFET_SWITCH = "low_side_mosfet_switch"
    RC_LOWPASS = "rc_lowpass"
    FLYBACK_DIODE = "flyback_diode"
    SERIES_ELEMENT = "series_element"


class CapacitorRole(str, Enum):
    DECOUPLING = "decoupling"
    BYPASS = "bypass"
    BULK = "bulk"
    RESERVOIR = "reservoir"
    FILTER = "filter"
    UNKNOWN_POWER_SHUNT = "unknown_power_shunt"


class TopologyPattern(BaseModel):
    pattern_type: PatternType
    component_refs: list[str]
    net_names: list[str]
    confidence: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class TopologyAnalysis(BaseModel):
    component_roles: dict[str, ComponentRole]
    component_role_sources: dict[str, str] = Field(default_factory=dict)
    component_role_reasons: dict[str, str] = Field(default_factory=dict)
    capacitor_roles: dict[str, CapacitorRole] = Field(default_factory=dict)
    capacitor_role_metadata: dict[str, dict[str, Any]] = Field(default_factory=dict)
    patterns: list[TopologyPattern]
    power_nets: set[str] = Field(default_factory=set)
    ground_nets: set[str] = Field(default_factory=set)
    source_nets: set[str] = Field(default_factory=set)
    load_nets: set[str] = Field(default_factory=set)

    def patterns_by_type(self, pattern_type: PatternType) -> list[TopologyPattern]:
        return [pattern for pattern in self.patterns if pattern.pattern_type == pattern_type]


GROUND_NET_NAMES = {"GND", "AGND", "DGND", "PGND", "SGND", "CHASSIS_GND", "0V", "VSS"}
POWER_NET_NAMES = {"VCC", "VDD", "VBAT", "VIN", "PANEL_POS", "SYS", "3V3", "5V", "12V", "-12V", "+5V", "+3V3", "V+", "V-"}
MIN_PLACEMENT_PATTERN_CONFIDENCE = 0.75


class TopologyAnalyzer:
    def analyze(self, circuit: Circuit, library: ComponentLibrary) -> TopologyAnalysis:
        context = _TopologyContext(circuit, library)
        decisions = {component.ref: self._infer_role_decision(component, context.definition(component.ref), context) for component in sorted(circuit.components, key=lambda item: item.ref)}
        roles = {ref: decision[0] for ref, decision in decisions.items()}
        power_nets = self._power_nets(context)
        ground_nets = self._ground_nets(context)
        source_nets = self._source_nets(context)
        load_nets = self._load_nets(context, roles)
        capacitor_roles, capacitor_role_metadata = classify_capacitors(context, power_nets, ground_nets, roles)

        patterns: list[TopologyPattern] = []
        patterns.extend(detect_voltage_dividers(context, power_nets, ground_nets))
        divider_resistors = {ref for pattern in patterns if pattern.pattern_type == PatternType.VOLTAGE_DIVIDER for ref in pattern.component_refs}
        patterns.extend(detect_pull_resistors(context, power_nets, ground_nets, divider_resistors))
        pull_resistors = {ref for pattern in patterns if pattern.pattern_type in {PatternType.PULL_UP, PatternType.PULL_DOWN} for ref in pattern.component_refs}
        patterns.extend(detect_decoupling_capacitors(context, power_nets, ground_nets, roles, capacitor_roles, capacitor_role_metadata))
        patterns.extend(detect_series_gate_resistors(context, roles))
        patterns.extend(detect_led_current_limits(context, power_nets, ground_nets))
        patterns.extend(detect_low_side_mosfet_switches(context, ground_nets, patterns))
        classified = {ref for pattern in patterns for ref in pattern.component_refs}
        patterns.extend(detect_rc_lowpass(context, power_nets, ground_nets, classified))
        patterns.extend(detect_flyback_diodes(context))
        patterns.extend(detect_series_elements(context, ground_nets, classified | pull_resistors | divider_resistors))
        patterns.sort(key=lambda item: (item.pattern_type.value, item.component_refs, item.net_names))
        return TopologyAnalysis(
            component_roles=roles,
            component_role_sources={ref: decision[1] for ref, decision in decisions.items()},
            component_role_reasons={ref: decision[2] for ref, decision in decisions.items()},
            capacitor_roles=capacitor_roles,
            capacitor_role_metadata=capacitor_role_metadata,
            patterns=patterns,
            power_nets=set(sorted(power_nets)),
            ground_nets=set(sorted(ground_nets)),
            source_nets=set(sorted(source_nets)),
            load_nets=set(sorted(load_nets)),
        )

    def _infer_role(self, component: ComponentInstance, definition: ComponentDefinition | None, context: "_TopologyContext") -> ComponentRole:
        return self._infer_role_decision(component, definition, context)[0]

    def _infer_role_decision(self, component: ComponentInstance, definition: ComponentDefinition | None, context: "_TopologyContext") -> tuple[ComponentRole, str, str]:
        explicit = _explicit_role(component)
        if explicit:
            mapped = _role_from_text(explicit)
            if mapped:
                return mapped, "explicit", f"role={explicit}"
        if not definition:
            return ComponentRole.UNKNOWN, "fallback", "unknown component definition"
        component_id = component.component_id.upper()
        renderer = definition.body.renderer
        pin_names = {pin.name.upper() for pin in definition.pins}
        if component_id.startswith(("POWER_LIPO", "POWER_LIION")) or "BATTERY" in definition.name.upper():
            return ComponentRole.STORAGE, "library-derived", "battery component metadata"
        if "CHARGER" in component_id or {"VIN", "BAT"}.issubset(pin_names):
            return ComponentRole.CHARGER, "library-derived", "charger identifier or VIN/BAT pins"
        if component_id.startswith("POWER_") or any(pin.electrical_type == ElectricalType.power_out for pin in definition.pins):
            return ComponentRole.SOURCE, "library-derived", "power component or power_out pin"
        if definition.category == "MCU" or renderer in {"timer_555", "op_amp"}:
            return ComponentRole.CONTROLLER, "library-derived", "controller-like category or renderer"
        if renderer in {"nmos", "pmos"}:
            return ComponentRole.SWITCH, "library-derived", "switch renderer"
        if definition.category == "Lighting" or component_id.startswith("LIGHT_"):
            return ComponentRole.LOAD, "library-derived", "lighting/load category"
        if definition.category == "Basic" and renderer in {"resistor", "capacitor", "polarized_capacitor", "inductor"}:
            return ComponentRole.PASSIVE, "library-derived", "basic passive renderer"
        return ComponentRole.UNKNOWN, "fallback", "no role inference matched"

    def _power_nets(self, context: "_TopologyContext") -> set[str]:
        nets = {net.name for net in context.circuit.nets if _is_power_net_name(net.name)}
        for net in context.circuit.nets:
            for pinref in net.pins:
                pin = context.pin(pinref.component_ref, pinref.pin_name)
                if pin and pin.electrical_type == ElectricalType.power_out and pin.name.upper() not in {"NEG", "GND", "VSS"}:
                    nets.add(net.name)
        return nets

    def _ground_nets(self, context: "_TopologyContext") -> set[str]:
        nets = {net.name for net in context.circuit.nets if _is_ground_net_name(net.name)}
        for net in context.circuit.nets:
            if any((context.pin(pinref.component_ref, pinref.pin_name) and context.pin(pinref.component_ref, pinref.pin_name).name.upper() in {"GND", "NEG", "VSS"}) for pinref in net.pins):
                if net.name.upper() in GROUND_NET_NAMES:
                    nets.add(net.name)
        return nets

    def _source_nets(self, context: "_TopologyContext") -> set[str]:
        return {
            net.name
            for net in context.circuit.nets
            for pinref in net.pins
            if (context.pin(pinref.component_ref, pinref.pin_name) and context.pin(pinref.component_ref, pinref.pin_name).electrical_type == ElectricalType.power_out)
        }

    def _load_nets(self, context: "_TopologyContext", roles: dict[str, ComponentRole]) -> set[str]:
        return {net.name for net in context.circuit.nets for pinref in net.pins if roles.get(pinref.component_ref) == ComponentRole.LOAD}


class _TopologyContext:
    def __init__(self, circuit: Circuit, library: ComponentLibrary) -> None:
        self.circuit = circuit
        self.library = library
        self.instances = {component.ref: component for component in circuit.components}
        self.definitions = {component.ref: library.get(component.component_id) for component in circuit.components}
        self.net_by_name = {net.name: net for net in circuit.nets}
        self.nets_by_ref: dict[str, dict[str, str]] = {}
        self.pins_by_ref: dict[str, dict[str, PinDefinition]] = {}
        for net in circuit.nets:
            for pinref in net.pins:
                pin = self.pin(pinref.component_ref, pinref.pin_name)
                if not pin:
                    continue
                self.nets_by_ref.setdefault(pinref.component_ref, {})[pin.name] = net.name
                self.pins_by_ref.setdefault(pinref.component_ref, {})[pin.name] = pin

    def definition(self, ref: str) -> ComponentDefinition | None:
        return self.definitions.get(ref)

    def pin(self, ref: str, token: str) -> PinDefinition | None:
        definition = self.definitions.get(ref)
        return definition.resolve_pin(token) if definition else None

    def component_nets(self, ref: str) -> set[str]:
        return set(self.nets_by_ref.get(ref, {}).values())

    def net_for_pin(self, ref: str, pin_name: str) -> str | None:
        return self.nets_by_ref.get(ref, {}).get(pin_name)

    def pins_on_net(self, net_name: str) -> list[tuple[str, PinDefinition]]:
        net = self.net_by_name.get(net_name)
        if not net:
            return []
        result: list[tuple[str, PinDefinition]] = []
        for pinref in sorted(net.pins, key=lambda item: (item.component_ref, item.pin_name)):
            pin = self.pin(pinref.component_ref, pinref.pin_name)
            if pin:
                result.append((pinref.component_ref, pin))
        return result

    def two_pin_passives(self, renderer: str | None = None) -> list[ComponentInstance]:
        items: list[ComponentInstance] = []
        for component in sorted(self.circuit.components, key=lambda item: item.ref):
            definition = self.definition(component.ref)
            if not definition or len(definition.pins) != 2:
                continue
            if renderer and definition.body.renderer != renderer:
                continue
            if definition.body.renderer in {"resistor", "capacitor", "polarized_capacitor", "inductor"}:
                items.append(component)
        return items


def detect_voltage_dividers(context: _TopologyContext, power_nets: set[str], ground_nets: set[str]) -> list[TopologyPattern]:
    patterns: list[TopologyPattern] = []
    resistors = context.two_pin_passives("resistor")
    for index, top in enumerate(resistors):
        top_nets = context.component_nets(top.ref)
        if len(top_nets) != 2:
            continue
        for bottom in resistors[index + 1 :]:
            bottom_nets = context.component_nets(bottom.ref)
            shared = sorted(top_nets & bottom_nets)
            if len(shared) != 1 or len(bottom_nets) != 2:
                continue
            midpoint = shared[0]
            top_outer = next(iter(top_nets - {midpoint}))
            bottom_outer = next(iter(bottom_nets - {midpoint}))
            high, low, top_ref, bottom_ref = _divider_order(top.ref, bottom.ref, top_outer, bottom_outer, power_nets, ground_nets)
            if not high or not low:
                continue
            sense_pins = [
                {"component_ref": ref, "pin_name": pin.name}
                for ref, pin in context.pins_on_net(midpoint)
                if ref not in {top.ref, bottom.ref} and pin.electrical_type in {ElectricalType.input, ElectricalType.bidirectional}
            ]
            patterns.append(
                TopologyPattern(
                    pattern_type=PatternType.VOLTAGE_DIVIDER,
                    component_refs=[top_ref, bottom_ref],
                    net_names=[high, midpoint, low],
                    confidence=0.9,
                    metadata={
                        "top_resistor": top_ref,
                        "bottom_resistor": bottom_ref,
                        "high_net": high,
                        "midpoint_net": midpoint,
                        "low_net": low,
                        "sense_pins": sorted(sense_pins, key=lambda item: (item["component_ref"], item["pin_name"])),
                    },
                )
            )
    return patterns


def detect_pull_resistors(context: _TopologyContext, power_nets: set[str], ground_nets: set[str], excluded_resistors: set[str]) -> list[TopologyPattern]:
    patterns: list[TopologyPattern] = []
    for resistor in context.two_pin_passives("resistor"):
        if resistor.ref in excluded_resistors:
            continue
        nets = sorted(context.component_nets(resistor.ref))
        if len(nets) != 2:
            continue
        a, b = nets
        if a in ground_nets or b in ground_nets:
            signal = b if a in ground_nets else a
            if _net_has_high_impedance_pin(context, signal, resistor.ref) and not _net_has_capacitor_to_ground(context, signal, ground_nets):
                patterns.append(_pull_pattern(PatternType.PULL_DOWN, resistor.ref, signal, a if a in ground_nets else b, 0.85))
        if a in power_nets or b in power_nets:
            signal = b if a in power_nets else a
            if _net_has_high_impedance_pin(context, signal, resistor.ref) and not _net_has_capacitor_to_ground(context, signal, ground_nets):
                patterns.append(_pull_pattern(PatternType.PULL_UP, resistor.ref, signal, a if a in power_nets else b, 0.85))
    return patterns


def classify_capacitors(
    context: _TopologyContext,
    power_nets: set[str],
    ground_nets: set[str],
    roles: dict[str, ComponentRole],
) -> tuple[dict[str, CapacitorRole], dict[str, dict[str, Any]]]:
    capacitor_roles: dict[str, CapacitorRole] = {}
    metadata: dict[str, dict[str, Any]] = {}
    for capacitor in context.two_pin_passives():
        definition = context.definition(capacitor.ref)
        if not definition or definition.body.renderer not in {"capacitor", "polarized_capacitor"}:
            continue
        nets = sorted(context.component_nets(capacitor.ref))
        if len(nets) != 2 or not (set(nets) & power_nets) or not (set(nets) & ground_nets):
            continue
        power = next(net for net in nets if net in power_nets)
        ground = next(net for net in nets if net in ground_nets)
        targets = [
            ref
            for ref, role in sorted(roles.items())
            if role in {ComponentRole.CONTROLLER, ComponentRole.CHARGER, ComponentRole.REGULATOR} and {power, ground}.issubset(context.component_nets(ref))
        ]
        targets.sort(key=lambda ref: (0 if roles.get(ref) == ComponentRole.CONTROLLER else 1, ref))
        capacitance = _capacitance_f(capacitor)
        role, confidence, reason = _classify_capacitor_role(
            normalize_role_text(_explicit_role(capacitor) or ""),
            capacitance,
            bool(targets),
            _net_has_source_or_storage(context, power),
        )
        capacitor_roles[capacitor.ref] = role
        metadata[capacitor.ref] = {
            "capacitor": capacitor.ref,
            "role": role.value,
            "confidence": confidence,
            "reason": reason,
            "capacitance_f": capacitance,
            "power_net": power,
            "ground_net": ground,
            "target_component": targets[0] if targets else None,
        }
    return capacitor_roles, metadata


def detect_decoupling_capacitors(
    context: _TopologyContext,
    power_nets: set[str],
    ground_nets: set[str],
    roles: dict[str, ComponentRole],
    capacitor_roles: dict[str, CapacitorRole],
    capacitor_role_metadata: dict[str, dict[str, Any]],
) -> list[TopologyPattern]:
    patterns: list[TopologyPattern] = []
    for capacitor in context.two_pin_passives():
        definition = context.definition(capacitor.ref)
        if not definition or definition.body.renderer not in {"capacitor", "polarized_capacitor"}:
            continue
        role = capacitor_roles.get(capacitor.ref)
        role_metadata = capacitor_role_metadata.get(capacitor.ref, {})
        if role not in {CapacitorRole.DECOUPLING, CapacitorRole.BYPASS} or float(role_metadata.get("confidence", 0.0)) < 0.75:
            continue
        nets = sorted(context.component_nets(capacitor.ref))
        if len(nets) != 2 or not (set(nets) & power_nets) or not (set(nets) & ground_nets):
            continue
        power = next(net for net in nets if net in power_nets)
        ground = next(net for net in nets if net in ground_nets)
        targets = [
            ref
            for ref, role_candidate in sorted(roles.items())
            if role_candidate in {ComponentRole.CONTROLLER, ComponentRole.CHARGER, ComponentRole.REGULATOR} and {power, ground}.issubset(context.component_nets(ref))
        ]
        targets.sort(key=lambda ref: (0 if roles.get(ref) == ComponentRole.CONTROLLER else 1, ref))
        patterns.append(
            TopologyPattern(
                pattern_type=PatternType.DECOUPLING_CAPACITOR,
                component_refs=[capacitor.ref, *targets[:1]],
                net_names=[power, ground],
                confidence=float(role_metadata.get("confidence", 0.75)),
                metadata=role_metadata,
            )
        )
    return patterns


def detect_series_gate_resistors(context: _TopologyContext, roles: dict[str, ComponentRole]) -> list[TopologyPattern]:
    patterns: list[TopologyPattern] = []
    for mosfet in sorted((ref for ref, role in roles.items() if role == ComponentRole.SWITCH), key=str):
        gate_net = context.net_for_pin(mosfet, "G")
        if not gate_net:
            continue
        for resistor in context.two_pin_passives("resistor"):
            nets = context.component_nets(resistor.ref)
            if gate_net not in nets or len(nets) != 2:
                continue
            driver_net = next(iter(nets - {gate_net}))
            driver = _driver_on_net(context, roles, driver_net, resistor.ref)
            if driver:
                patterns.append(
                    TopologyPattern(
                        pattern_type=PatternType.SERIES_GATE_RESISTOR,
                        component_refs=[driver["component_ref"], resistor.ref, mosfet],
                        net_names=[driver_net, gate_net],
                        confidence=0.92,
                        metadata={"driver_component": driver["component_ref"], "driver_pin": driver["pin_name"], "gate_resistor": resistor.ref, "mosfet": mosfet, "gate_net": gate_net},
                    )
                )
    return patterns


def detect_led_current_limits(context: _TopologyContext, power_nets: set[str], ground_nets: set[str]) -> list[TopologyPattern]:
    patterns: list[TopologyPattern] = []
    leds = [component for component in sorted(context.circuit.components, key=lambda item: item.ref) if component.component_id.startswith("LIGHT_LED_")]
    for led in leds:
        led_nets = context.component_nets(led.ref)
        for resistor in context.two_pin_passives("resistor"):
            resistor_nets = context.component_nets(resistor.ref)
            shared = sorted(led_nets & resistor_nets)
            if len(shared) != 1:
                continue
            other = next(iter(resistor_nets - set(shared)))
            confidence = 0.9 if other in power_nets or other in ground_nets or _net_has_switch_or_source(context, other) else 0.75
            patterns.append(
                TopologyPattern(
                    pattern_type=PatternType.LED_CURRENT_LIMIT,
                    component_refs=[resistor.ref, led.ref],
                    net_names=sorted(resistor_nets | led_nets),
                    confidence=confidence,
                    metadata={"resistor": resistor.ref, "led": led.ref, "shared_net": shared[0], "other_resistor_net": other},
                )
            )
    return patterns


def detect_low_side_mosfet_switches(context: _TopologyContext, ground_nets: set[str], patterns: list[TopologyPattern]) -> list[TopologyPattern]:
    gate_resistors = {pattern.metadata["mosfet"]: pattern for pattern in patterns if pattern.pattern_type == PatternType.SERIES_GATE_RESISTOR}
    led_limits = [pattern for pattern in patterns if pattern.pattern_type == PatternType.LED_CURRENT_LIMIT]
    result: list[TopologyPattern] = []
    for component in sorted(context.circuit.components, key=lambda item: item.ref):
        definition = context.definition(component.ref)
        if not definition or definition.body.renderer != "nmos":
            continue
        source_net = context.net_for_pin(component.ref, "S")
        drain_net = context.net_for_pin(component.ref, "D")
        gate_net = context.net_for_pin(component.ref, "G")
        if source_net not in ground_nets or not drain_net or not gate_net:
            continue
        load_pattern = next((pattern for pattern in led_limits if drain_net in pattern.net_names), None)
        gate_pattern = gate_resistors.get(component.ref)
        pulldown = next((ref for ref in _resistors_between(context, gate_net, source_net or "") if ref != (gate_pattern.metadata.get("gate_resistor") if gate_pattern else "")), None)
        refs = [component.ref]
        if load_pattern:
            refs.extend(load_pattern.component_refs)
        if gate_pattern:
            refs.append(gate_pattern.metadata["gate_resistor"])
            refs.append(gate_pattern.metadata["driver_component"])
        if pulldown:
            refs.append(pulldown)
        result.append(
            TopologyPattern(
                pattern_type=PatternType.LOW_SIDE_MOSFET_SWITCH,
                component_refs=sorted(set(refs)),
                net_names=sorted({source_net, drain_net, gate_net}),
                confidence=0.9 if load_pattern and gate_pattern else 0.8,
                metadata={"mosfet": component.ref, "source_net": source_net, "drain_net": drain_net, "gate_net": gate_net, "load_pattern": load_pattern.model_dump(mode="json") if load_pattern else None, "gate_resistor": gate_pattern.metadata.get("gate_resistor") if gate_pattern else None, "pull_down": pulldown},
            )
        )
    return result


def detect_rc_lowpass(context: _TopologyContext, power_nets: set[str], ground_nets: set[str], excluded_components: set[str]) -> list[TopologyPattern]:
    patterns: list[TopologyPattern] = []
    for capacitor in context.two_pin_passives():
        definition = context.definition(capacitor.ref)
        if not definition or definition.body.renderer not in {"capacitor", "polarized_capacitor"} or capacitor.ref in excluded_components:
            continue
        cap_nets = context.component_nets(capacitor.ref)
        if len(cap_nets) != 2 or not cap_nets & ground_nets:
            continue
        if cap_nets & power_nets:
            continue
        output_net = next(iter(cap_nets - ground_nets))
        for resistor in context.two_pin_passives("resistor"):
            if resistor.ref in excluded_components:
                continue
            resistor_nets = context.component_nets(resistor.ref)
            if output_net in resistor_nets and len(resistor_nets) == 2:
                input_net = next(iter(resistor_nets - {output_net}))
                patterns.append(
                    TopologyPattern(
                        pattern_type=PatternType.RC_LOWPASS,
                        component_refs=[resistor.ref, capacitor.ref],
                        net_names=[input_net, output_net, next(iter(cap_nets & ground_nets))],
                        confidence=0.8,
                        metadata={"resistor": resistor.ref, "capacitor": capacitor.ref, "input_net": input_net, "output_net": output_net},
                    )
                )
    return patterns


def detect_flyback_diodes(context: _TopologyContext) -> list[TopologyPattern]:
    diode_refs = [component.ref for component in context.circuit.components if "DIODE" in component.component_id.upper()]
    inductive_refs = [component.ref for component in context.circuit.components if context.definition(component.ref) and context.definition(component.ref).body.renderer == "inductor"]
    patterns: list[TopologyPattern] = []
    for diode in sorted(diode_refs):
        diode_nets = context.component_nets(diode)
        for load in sorted(inductive_refs):
            if diode_nets == context.component_nets(load) and len(diode_nets) == 2:
                patterns.append(TopologyPattern(pattern_type=PatternType.FLYBACK_DIODE, component_refs=[diode, load], net_names=sorted(diode_nets), confidence=0.9, metadata={"diode": diode, "load": load}))
    return patterns


def detect_series_elements(context: _TopologyContext, ground_nets: set[str], excluded_components: set[str]) -> list[TopologyPattern]:
    patterns: list[TopologyPattern] = []
    for component in context.two_pin_passives():
        if component.ref in excluded_components:
            continue
        nets = sorted(context.component_nets(component.ref))
        if len(nets) == 2 and not set(nets) & ground_nets:
            patterns.append(TopologyPattern(pattern_type=PatternType.SERIES_ELEMENT, component_refs=[component.ref], net_names=nets, confidence=0.75, metadata={"component": component.ref}))
    return patterns


def _divider_order(a_ref: str, b_ref: str, a_outer: str, b_outer: str, power_nets: set[str], ground_nets: set[str]) -> tuple[str | None, str | None, str, str]:
    if a_outer in power_nets and b_outer in ground_nets:
        return a_outer, b_outer, a_ref, b_ref
    if b_outer in power_nets and a_outer in ground_nets:
        return b_outer, a_outer, b_ref, a_ref
    return None, None, a_ref, b_ref


def _pull_pattern(pattern_type: PatternType, resistor: str, signal: str, rail: str, confidence: float) -> TopologyPattern:
    return TopologyPattern(pattern_type=pattern_type, component_refs=[resistor], net_names=[signal, rail], confidence=confidence, metadata={"resistor": resistor, "signal_net": signal, "rail_net": rail})


def _net_has_high_impedance_pin(context: _TopologyContext, net_name: str, resistor_ref: str) -> bool:
    for ref, pin in context.pins_on_net(net_name):
        if ref == resistor_ref:
            continue
        if pin.name.upper() == "G" or pin.electrical_type in {ElectricalType.input, ElectricalType.bidirectional, ElectricalType.open_drain}:
            return True
    return False


def _net_has_capacitor_to_ground(context: _TopologyContext, net_name: str, ground_nets: set[str]) -> bool:
    for component in context.two_pin_passives():
        definition = context.definition(component.ref)
        if not definition or definition.body.renderer not in {"capacitor", "polarized_capacitor"}:
            continue
        nets = context.component_nets(component.ref)
        if net_name in nets and bool(nets & ground_nets):
            return True
    return False


def _driver_on_net(context: _TopologyContext, roles: dict[str, ComponentRole], net_name: str, resistor_ref: str) -> dict[str, str] | None:
    for ref, pin in context.pins_on_net(net_name):
        if ref == resistor_ref:
            continue
        if roles.get(ref) == ComponentRole.CONTROLLER and pin.electrical_type in {ElectricalType.output, ElectricalType.bidirectional, ElectricalType.input}:
            return {"component_ref": ref, "pin_name": pin.name}
    return None


def _net_has_switch_or_source(context: _TopologyContext, net_name: str) -> bool:
    for ref, pin in context.pins_on_net(net_name):
        definition = context.definition(ref)
        if definition and (definition.body.renderer in {"nmos", "pmos"} or pin.electrical_type == ElectricalType.power_out):
            return True
    return False


def _resistors_between(context: _TopologyContext, a: str, b: str) -> list[str]:
    return [resistor.ref for resistor in context.two_pin_passives("resistor") if context.component_nets(resistor.ref) == {a, b}]


def _capacitance_f(component: ComponentInstance) -> float | None:
    value = component.parameters.get("value")
    numeric = getattr(value, "numeric", None)
    unit = getattr(value, "unit", None)
    return float(numeric) if numeric is not None and unit == "F" else None


def _classify_capacitor_role(explicit: str, capacitance_f: float | None, has_ic_target: bool, near_source_or_storage: bool) -> tuple[CapacitorRole, float, str]:
    explicit_map = {
        "decoupling": CapacitorRole.DECOUPLING,
        "bypass": CapacitorRole.BYPASS,
        "control_bypass": CapacitorRole.BYPASS,
        "bulk": CapacitorRole.BULK,
        "reservoir": CapacitorRole.RESERVOIR,
        "filter": CapacitorRole.FILTER,
    }
    if explicit in explicit_map:
        return explicit_map[explicit], 1.0, f"explicit role={explicit}"
    if capacitance_f is None:
        if has_ic_target:
            return CapacitorRole.UNKNOWN_POWER_SHUNT, 0.6, "power shunt near IC but capacitance missing or malformed"
        return CapacitorRole.UNKNOWN_POWER_SHUNT, 0.5, "power shunt with no strong role evidence"
    if has_ic_target and capacitance_f <= 220e-9:
        return CapacitorRole.DECOUPLING, 0.9, "small capacitor across an IC power domain"
    if has_ic_target and capacitance_f <= 4.7e-6:
        return CapacitorRole.BYPASS, 0.82, "moderate local bypass capacitor across an IC power domain"
    if near_source_or_storage and capacitance_f >= 470e-6:
        return CapacitorRole.RESERVOIR, 0.85, "large capacitor at source or storage rail"
    if near_source_or_storage and capacitance_f >= 10e-6:
        return CapacitorRole.BULK, 0.8, "bulk capacitor at source or storage rail"
    if capacitance_f >= 47e-6:
        return CapacitorRole.BULK, 0.7, "large rail shunt capacitor"
    return CapacitorRole.UNKNOWN_POWER_SHUNT, 0.6, "ambiguous power shunt capacitor"


def _net_has_source_or_storage(context: _TopologyContext, net_name: str) -> bool:
    for ref, pin in context.pins_on_net(net_name):
        instance = context.instances.get(ref)
        definition = context.definition(ref)
        if not instance or not definition:
            continue
        if instance.component_id.startswith(("POWER_", "POWER_LIPO", "POWER_LIION")) or pin.electrical_type == ElectricalType.power_out:
            return True
    return False


def _explicit_role(component: ComponentInstance) -> str | None:
    value = component.parameters.get("role")
    if value is None:
        return None
    return str(getattr(value, "original", value)).lower()


def _role_from_text(text: str) -> ComponentRole | None:
    normalized = normalize_role_text(text)
    for role in ComponentRole:
        if normalized == role.value:
            return role
    if "source" in normalized or normalized in {"supply"}:
        return ComponentRole.SOURCE
    if "storage" in normalized or "battery" in normalized:
        return ComponentRole.STORAGE
    if "charger" in normalized:
        return ComponentRole.CHARGER
    if "controller" in normalized or "oscillator" in normalized or "amplifier" in normalized:
        return ComponentRole.CONTROLLER
    if "switch" in normalized:
        return ComponentRole.SWITCH
    if "load" in normalized or "output" in normalized:
        return ComponentRole.LOAD
    if normalized in {"timing", "feedback", "input", "pulldown", "pullup", "pull_down", "pull_up", "current_limit", "gate_resistor", "decoupling", "control_bypass"}:
        return ComponentRole.PASSIVE
    return None


KNOWN_LOCAL_ROLE_INTENTS = {
    "astable_oscillator",
    "control_bypass",
    "current_limit",
    "decoupling",
    "bulk",
    "bypass",
    "divider_bottom",
    "divider_top",
    "feedback",
    "gate_resistor",
    "input",
    "inverting_amplifier",
    "low_side_switch",
    "negative_supply",
    "oscillator_output",
    "output_feedback",
    "positive_supply",
    "pulldown",
    "pullup",
    "pull_down",
    "pull_up",
    "signal_source",
    "reservoir",
    "filter",
    "supply",
    "timing",
}


def normalize_role_text(text: str) -> str:
    return text.strip().lower().replace("-", "_").replace(" ", "_")


def is_supported_role_text(text: str) -> bool:
    normalized = normalize_role_text(text)
    return normalized in KNOWN_LOCAL_ROLE_INTENTS or any(normalized == role.value for role in ComponentRole)


def _is_ground_net_name(name: str) -> bool:
    return name.upper() in GROUND_NET_NAMES


def _is_power_net_name(name: str) -> bool:
    upper = name.upper()
    if _is_ground_net_name(upper):
        return False
    if upper in POWER_NET_NAMES:
        return True
    if upper.endswith("_SENSE") or "SENSE" in upper:
        return False
    return bool(re.fullmatch(r"[+-]?\d+(?:V\d*)?", upper))
