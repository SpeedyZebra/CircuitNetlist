from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .component_library import ComponentLibrary
from .models import Circuit, ComponentInstance, Diagnostic, ElectricalType, EngineeringValue, PinDefinition, PinIntent, Severity

GROUND_NETS = {"GND", "AGND", "DGND", "PGND", "0V", "VSS"}
POSITIVE_NETS = {"VBAT", "VDD", "VCC", "VIN", "VOUT", "PANEL_POS", "SYS", "BAT", "+5V", "5V", "+3V3", "3V3", "12V", "+12V"}
POWER_SOURCE_INTENTS = {
    PinIntent.source_positive,
    PinIntent.battery_positive,
    PinIntent.solar_positive,
    PinIntent.regulator_output,
}
POSITIVE_PIN_INTENTS = {
    PinIntent.supply_positive,
    PinIntent.source_positive,
    PinIntent.battery_positive,
    PinIntent.solar_positive,
    PinIntent.charger_input,
    PinIntent.charger_battery,
    PinIntent.regulator_input,
    PinIntent.regulator_output,
    PinIntent.op_amp_positive_supply,
}
GROUND_PIN_INTENTS = {
    PinIntent.supply_ground,
    PinIntent.source_negative,
    PinIntent.battery_negative,
    PinIntent.solar_negative,
    PinIntent.op_amp_negative_supply,
}
REQUIRED_POWER_INTENTS = POSITIVE_PIN_INTENTS | GROUND_PIN_INTENTS
ERC_SAFE_VALIDATION_CODES = {"VALIDATION_PIN_MULTIPLE_NETS"}


@dataclass(frozen=True)
class ResolvedPin:
    ref: str
    component_id: str
    net_name: str
    pin: PinDefinition

    @property
    def intent(self) -> PinIntent | None:
        return self.pin.intent


@dataclass
class ErcContext:
    circuit: Circuit
    library: ComponentLibrary
    components: dict[str, ComponentInstance]
    refs: dict[str, str]
    pin_to_net: dict[tuple[str, str], str]
    pin_name_to_net: dict[tuple[str, str], str]
    net_pins: dict[str, list[ResolvedPin]]


class ElectricalRuleChecker:
    """Compatibility wrapper for the shared production ERC engine."""

    def __init__(self, library: ComponentLibrary) -> None:
        self.library = library

    def check(self, circuit: Circuit) -> list[Diagnostic]:
        return run_electrical_rules(circuit, self.library)


def run_electrical_rules(circuit: Circuit, library: ComponentLibrary, topology: Any | None = None, options: dict[str, Any] | None = None) -> list[Diagnostic]:
    context = _build_context(circuit, library)
    diagnostics: list[Diagnostic] = []
    _check_single_pin_nets(circuit, diagnostics)
    _check_net_drive_conflicts(context, diagnostics)
    _check_required_pins(context, diagnostics)
    _check_terminal_shorts(context, diagnostics)
    _check_power_ground_conflicts(context, diagnostics)
    _check_pin_polarity(context, diagnostics)
    _check_led_current_limit(context, diagnostics)
    _check_mosfet_gate(context, diagnostics)
    _check_mosfet_pull_down(context, diagnostics)
    _check_topology_aware_rules(context, diagnostics)
    return _dedupe_diagnostics(diagnostics)


def can_run_electrical_rules(validation_diagnostics: list[Diagnostic]) -> bool:
    blocking = [diag for diag in validation_diagnostics if diag.severity in {Severity.ERROR, Severity.FATAL}]
    return not blocking or all(diag.code in ERC_SAFE_VALIDATION_CODES for diag in blocking)


def _build_context(circuit: Circuit, library: ComponentLibrary) -> ErcContext:
    components = {component.ref: component for component in circuit.components}
    refs = {component.ref: component.component_id for component in circuit.components}
    pin_to_net: dict[tuple[str, str], str] = {}
    pin_name_to_net: dict[tuple[str, str], str] = {}
    net_pins: dict[str, list[ResolvedPin]] = defaultdict(list)
    for net in circuit.nets:
        for pinref in net.pins:
            definition = library.get(refs.get(pinref.component_ref, ""))
            pin = definition.resolve_pin(pinref.pin_name) if definition else None
            if not pin:
                continue
            pin_to_net[(pinref.component_ref, pin.number)] = net.name
            pin_name_to_net[(pinref.component_ref, pin.name)] = net.name
            net_pins[net.name].append(ResolvedPin(pinref.component_ref, refs.get(pinref.component_ref, ""), net.name, pin))
    return ErcContext(circuit=circuit, library=library, components=components, refs=refs, pin_to_net=pin_to_net, pin_name_to_net=pin_name_to_net, net_pins=net_pins)


def _check_single_pin_nets(circuit: Circuit, diagnostics: list[Diagnostic]) -> None:
    for net in circuit.nets:
        if len(net.pins) == 1 and not net.allow_single:
            diagnostics.append(_diag(Severity.WARNING, "ERC_SINGLE_PIN_NET", f"Net {net.name} has only one connection", net_name=net.name))


def _check_net_drive_conflicts(context: ErcContext, diagnostics: list[Diagnostic]) -> None:
    for net_name, pins in context.net_pins.items():
        outputs = [pin for pin in pins if pin.pin.electrical_type == ElectricalType.output]
        power_outputs = [pin for pin in pins if pin.pin.electrical_type == ElectricalType.power_out]
        power_inputs = [pin for pin in pins if pin.pin.electrical_type == ElectricalType.power_in]
        if len(outputs) > 1:
            diagnostics.append(_diag(Severity.ERROR, "ERC_OUTPUT_CONFLICT", f"Two push-pull outputs connected on {net_name}", net_name=net_name))
        if len(power_outputs) > 1:
            diagnostics.append(_diag(Severity.ERROR, "ERC_POWER_OUTPUT_CONFLICT", f"Multiple power outputs connected on {net_name}", net_name=net_name))
        if power_inputs and _is_positive_net(context, net_name) and not _net_has_power_source(context, net_name):
            diagnostics.append(_diag(Severity.WARNING, "ERC_POWER_INPUT_NO_SOURCE", f"Power-input pin on {net_name} has no apparent source", net_name=net_name))


def _check_required_pins(context: ErcContext, diagnostics: list[Diagnostic]) -> None:
    for component in context.circuit.components:
        definition = context.library.get(component.component_id)
        if not definition:
            continue
        for pin in definition.pins:
            if pin.electrical_type == ElectricalType.no_connect or not pin.required:
                continue
            connected = (component.ref, pin.number) in context.pin_to_net
            if connected:
                continue
            if pin.intent in REQUIRED_POWER_INTENTS or pin.name in {"GND", "VDD", "VCC", "VIN", "VOUT", "BAT", "SYS", "POS", "NEG", "V+", "V-"}:
                diagnostics.append(_diag(Severity.ERROR, "ERC_REQUIRED_PIN_UNCONNECTED", f"{component.ref}.{pin.name} is not connected", component_ref=component.ref, pin_ref=pin.name))
            elif pin.intent in {PinIntent.reset_pin, PinIntent.control_pin}:
                diagnostics.append(_diag(Severity.WARNING, "ERC_CONTROL_PIN_FLOATING", f"{component.ref}.{pin.name} control pin is floating", component_ref=component.ref, pin_ref=pin.name))


def _check_terminal_shorts(context: ErcContext, diagnostics: list[Diagnostic]) -> None:
    for component in context.circuit.components:
        definition = context.library.get(component.component_id)
        if not definition:
            continue
        pins = _component_pin_nets(context, component.ref)
        for pin_a, pin_b, code, label in _terminal_short_pairs(component.component_id, definition.pins):
            net_a = pins.get(pin_a)
            net_b = pins.get(pin_b)
            if net_a and net_b and net_a == net_b:
                diagnostics.append(_diag(Severity.ERROR, code, f"{component.ref} {label} terminals are shorted together", component_ref=component.ref, net_name=net_a, metadata={"pins": [pin_a, pin_b]}))


def _terminal_short_pairs(component_id: str, pins: list[PinDefinition]) -> list[tuple[str, str, str, str]]:
    names = {pin.name for pin in pins}
    intents = {pin.intent: pin.name for pin in pins if pin.intent}
    pairs: list[tuple[str, str, str, str]] = []
    if PinIntent.source_positive in intents and PinIntent.source_negative in intents:
        pairs.append((intents[PinIntent.source_positive], intents[PinIntent.source_negative], "ERC_SOURCE_TERMINALS_SHORTED", "source"))
    if PinIntent.battery_positive in intents and PinIntent.battery_negative in intents:
        pairs.append((intents[PinIntent.battery_positive], intents[PinIntent.battery_negative], "ERC_BATTERY_TERMINALS_SHORTED", "battery"))
    if PinIntent.solar_positive in intents and PinIntent.solar_negative in intents:
        pairs.append((intents[PinIntent.solar_positive], intents[PinIntent.solar_negative], "ERC_SOLAR_TERMINALS_SHORTED", "solar"))
    if PinIntent.led_anode in intents and PinIntent.led_cathode in intents:
        pairs.append((intents[PinIntent.led_anode], intents[PinIntent.led_cathode], "ERC_LED_TERMINALS_SHORTED", "LED"))
    if PinIntent.diode_anode in intents and PinIntent.diode_cathode in intents:
        pairs.append((intents[PinIntent.diode_anode], intents[PinIntent.diode_cathode], "ERC_DIODE_TERMINALS_SHORTED", "diode"))
    if {"POS", "NEG"}.issubset(names) and "CAPACITOR" in component_id:
        pairs.append(("POS", "NEG", "ERC_CAPACITOR_TERMINALS_SHORTED", "capacitor"))
    supply_positive = next((pin.name for pin in pins if pin.intent in {PinIntent.supply_positive, PinIntent.op_amp_positive_supply}), None)
    supply_ground = next((pin.name for pin in pins if pin.intent in {PinIntent.supply_ground, PinIntent.op_amp_negative_supply}), None)
    if supply_positive and supply_ground:
        pairs.append((supply_positive, supply_ground, "ERC_SUPPLY_TERMINALS_SHORTED", "supply"))
    for critical in ("VIN", "BAT", "SYS", "VOUT"):
        if critical in names and "GND" in names:
            pairs.append((critical, "GND", "ERC_SUPPLY_TERMINALS_SHORTED", "supply"))
    return pairs


def _check_power_ground_conflicts(context: ErcContext, diagnostics: list[Diagnostic]) -> None:
    for net_name in context.net_pins:
        if _is_positive_net(context, net_name) and _is_ground_net(context, net_name):
            diagnostics.append(_diag(Severity.ERROR, "ERC_POWER_GND_SHORT", f"{net_name} contains both positive-rail and ground/return connections", net_name=net_name))
            diagnostics.append(_diag(Severity.ERROR, "ERC_SUPPLY_NET_CONFLICT", f"{net_name} is classified as both a positive rail and ground", net_name=net_name))


def _check_pin_polarity(context: ErcContext, diagnostics: list[Diagnostic]) -> None:
    for net_name, pins in context.net_pins.items():
        positive_net = _is_positive_net(context, net_name)
        ground_net = _is_ground_net(context, net_name)
        for resolved in pins:
            intent = resolved.intent
            if not intent:
                continue
            negative_source = _is_negative_supply_source(context, resolved.ref)
            if ground_net and intent in {PinIntent.supply_positive, PinIntent.charger_input, PinIntent.charger_battery, PinIntent.regulator_input, PinIntent.regulator_output, PinIntent.op_amp_positive_supply}:
                diagnostics.append(_diag(Severity.ERROR, "ERC_POSITIVE_SUPPLY_PIN_ON_GROUND", f"{resolved.ref}.{resolved.pin.name} positive supply pin is connected to ground net {net_name}", component_ref=resolved.ref, pin_ref=resolved.pin.name, net_name=net_name))
            elif ground_net and intent in {PinIntent.source_positive, PinIntent.battery_positive, PinIntent.solar_positive} and not (intent == PinIntent.source_positive and negative_source):
                diagnostics.append(_diag(Severity.ERROR, "ERC_POSITIVE_TERMINAL_ON_GROUND", f"{resolved.ref}.{resolved.pin.name} positive terminal is connected to ground net {net_name}", component_ref=resolved.ref, pin_ref=resolved.pin.name, net_name=net_name))
            elif positive_net and intent in {PinIntent.supply_ground}:
                diagnostics.append(_diag(Severity.ERROR, "ERC_GROUND_PIN_ON_POSITIVE_RAIL", f"{resolved.ref}.{resolved.pin.name} ground pin is connected to positive rail {net_name}", component_ref=resolved.ref, pin_ref=resolved.pin.name, net_name=net_name))
            elif positive_net and intent in {PinIntent.source_negative, PinIntent.battery_negative, PinIntent.solar_negative} and not (intent == PinIntent.source_negative and negative_source):
                diagnostics.append(_diag(Severity.ERROR, "ERC_NEGATIVE_TERMINAL_ON_POSITIVE_RAIL", f"{resolved.ref}.{resolved.pin.name} negative terminal is connected to positive rail {net_name}", component_ref=resolved.ref, pin_ref=resolved.pin.name, net_name=net_name))
            elif positive_net and intent == PinIntent.op_amp_negative_supply:
                diagnostics.append(_diag(Severity.ERROR, "ERC_SUPPLY_POLARITY_REVERSED", f"{resolved.ref}.{resolved.pin.name} negative supply is connected to positive rail {net_name}", component_ref=resolved.ref, pin_ref=resolved.pin.name, net_name=net_name))


def _check_led_current_limit(context: ErcContext, diagnostics: list[Diagnostic]) -> None:
    resistive_nets = {net_name for net_name, pins in context.net_pins.items() for pin in pins if pin.component_id == "BASIC_RESISTOR"}
    for component in context.circuit.components:
        if component.component_id.startswith("LIGHT_LED_") and component.component_id != "LIGHT_LED_RGB_ADDRESSABLE" and component.parameters.get("internally_regulated") != "true":
            led_nets = _component_nets(context, component.ref)
            if not led_nets & resistive_nets:
                diagnostics.append(_diag(Severity.WARNING, "ERC_LED_NO_CURRENT_LIMIT", f"{component.ref} LED appears to lack a current-limiting resistor", component_ref=component.ref))
            anode_net = context.pin_name_to_net.get((component.ref, "A"))
            cathode_net = context.pin_name_to_net.get((component.ref, "K"))
            if (anode_net and _is_ground_net(context, anode_net)) or (cathode_net and _is_positive_net(context, cathode_net)):
                diagnostics.append(_diag(Severity.WARNING, "ERC_LED_POLARITY_REVERSED", f"{component.ref} LED polarity appears reversed", component_ref=component.ref, metadata={"anode_net": anode_net, "cathode_net": cathode_net}))


def _check_mosfet_gate(context: ErcContext, diagnostics: list[Diagnostic]) -> None:
    for component in context.circuit.components:
        if component.component_id in {"BASIC_NMOS", "BASIC_PMOS"}:
            definition = context.library.get(component.component_id)
            gate = definition.resolve_pin("G") if definition else None
            if gate and (component.ref, gate.number) not in context.pin_to_net:
                diagnostics.append(_diag(Severity.ERROR, "ERC_MOSFET_GATE_FLOATING", f"{component.ref} MOSFET gate is floating", component_ref=component.ref, pin_ref="G"))


def _check_mosfet_pull_down(context: ErcContext, diagnostics: list[Diagnostic]) -> None:
    resistor_refs = [component.ref for component in context.circuit.components if component.component_id == "BASIC_RESISTOR"]
    resistor_nets = {ref: _component_nets(context, ref) for ref in resistor_refs}
    for component in context.circuit.components:
        if component.component_id != "BASIC_NMOS":
            continue
        gate_net = context.pin_name_to_net.get((component.ref, "G"))
        source_net = context.pin_name_to_net.get((component.ref, "S"))
        if source_net and not _is_ground_net(context, source_net):
            diagnostics.append(_diag(Severity.WARNING, "ERC_MOSFET_SOURCE_NOT_GROUND", f"{component.ref} source is not tied to ground", component_ref=component.ref, pin_ref="S", net_name=source_net))
        if gate_net and _is_positive_net(context, gate_net):
            diagnostics.append(_diag(Severity.WARNING, "ERC_MOSFET_GATE_TIED_TO_POWER", f"{component.ref} gate is tied directly to {gate_net}", component_ref=component.ref, net_name=gate_net))
        if not gate_net:
            continue
        has_pulldown = any(gate_net in nets and any(_is_ground_net(context, net) for net in nets) for nets in resistor_nets.values())
        if not has_pulldown:
            adjacent_nets = set()
            for nets in resistor_nets.values():
                if gate_net in nets:
                    adjacent_nets |= nets - {gate_net}
            has_pulldown = any(bool(adjacent_nets & nets) and any(_is_ground_net(context, net) for net in nets) for nets in resistor_nets.values())
        if not has_pulldown:
            diagnostics.append(_diag(Severity.WARNING, "ERC_MOSFET_GATE_NO_PULLDOWN", f"{component.ref} gate net lacks a pull-down resistor", component_ref=component.ref, net_name=gate_net))


def _check_topology_aware_rules(context: ErcContext, diagnostics: list[Diagnostic]) -> None:
    divider_top = [component for component in context.circuit.components if _role_of(component) == "divider_top"]
    divider_bottom = [component for component in context.circuit.components if _role_of(component) == "divider_bottom"]
    if divider_top and not divider_bottom:
        diagnostics.append(_diag(Severity.ERROR, "ERC_DIVIDER_BOTTOM_MISSING", "Divider top resistor exists without a divider bottom resistor", component_ref=divider_top[0].ref))
    for top in divider_top:
        top_nets = _component_nets(context, top.ref)
        for bottom in divider_bottom:
            bottom_nets = _component_nets(context, bottom.ref)
            midpoint = sorted((top_nets & bottom_nets) - (POSITIVE_NETS | GROUND_NETS))
            if not midpoint:
                continue
            midpoint_net = midpoint[0]
            if _is_ground_net(context, midpoint_net):
                diagnostics.append(_diag(Severity.ERROR, "ERC_DIVIDER_MIDPOINT_GROUNDED", f"Divider midpoint {midpoint_net} is shorted to ground", net_name=midpoint_net))
            has_adc = any(pin.component_id.startswith("MCU_") for pin in context.net_pins.get(midpoint_net, []))
            if not has_adc:
                diagnostics.append(_diag(Severity.WARNING, "ERC_ADC_PIN_UNCONNECTED", f"Divider midpoint {midpoint_net} is not connected to an MCU ADC/input pin", net_name=midpoint_net))


def _component_pin_nets(context: ErcContext, ref: str) -> dict[str, str]:
    return {pin_name: net_name for (pin_ref, pin_name), net_name in context.pin_name_to_net.items() if pin_ref == ref}


def _component_nets(context: ErcContext, ref: str) -> set[str]:
    return {net_name for (pin_ref, _), net_name in context.pin_name_to_net.items() if pin_ref == ref}


def _net_has_power_source(context: ErcContext, net_name: str) -> bool:
    return any(pin.pin.electrical_type == ElectricalType.power_out or pin.intent in POWER_SOURCE_INTENTS for pin in context.net_pins.get(net_name, []))


def _is_ground_net(context: ErcContext, net_name: str) -> bool:
    upper = net_name.upper()
    return upper in GROUND_NETS or any(pin.intent in GROUND_PIN_INTENTS and not (pin.intent == PinIntent.source_negative and _is_negative_supply_source(context, pin.ref)) for pin in context.net_pins.get(net_name, []))


def _is_positive_net(context: ErcContext, net_name: str) -> bool:
    upper = net_name.upper()
    return upper in POSITIVE_NETS or any(pin.intent in POSITIVE_PIN_INTENTS and not (pin.intent == PinIntent.source_positive and _is_negative_supply_source(context, pin.ref)) for pin in context.net_pins.get(net_name, []))


def _is_negative_supply_source(context: ErcContext, ref: str) -> bool:
    component = context.components.get(ref)
    if not component or component.component_id != "POWER_DC_SOURCE":
        return False
    role = _role_of(component).lower()
    voltage = _numeric_param(component, "voltage")
    return role == "negative_supply" or (voltage is not None and voltage < 0)


def _role_of(component: ComponentInstance) -> str:
    value = component.parameters.get("role")
    return value.original if isinstance(value, EngineeringValue) else str(value or "")


def _numeric_param(component: ComponentInstance, name: str) -> float | None:
    value = component.parameters.get(name)
    if isinstance(value, EngineeringValue):
        return value.numeric
    return None


def _diag(severity: Severity, code: str, message: str, **kwargs: Any) -> Diagnostic:
    metadata = dict(kwargs.pop("metadata", {}) or {})
    metadata.setdefault("subsystem", "erc")
    return Diagnostic(severity=severity, code=code, message=message, metadata=metadata, **kwargs)


def _dedupe_diagnostics(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    seen: set[tuple[str | None, str | None, str | None, str | None, str]] = set()
    result: list[Diagnostic] = []
    for diagnostic in diagnostics:
        key = (diagnostic.code, diagnostic.component_ref, diagnostic.pin_ref, diagnostic.net_name, diagnostic.message)
        if key in seen:
            continue
        seen.add(key)
        result.append(diagnostic)
    return result
