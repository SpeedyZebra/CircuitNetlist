from __future__ import annotations

from collections import defaultdict

from .component_library import ComponentLibrary
from .models import Circuit, Diagnostic, ElectricalType, Severity

GROUND_NETS = {"GND", "AGND", "DGND", "PGND"}
SOURCE_LIKE_NETS = {"VBAT", "VDD", "VCC", "VSS", "VEE", "SYS", "BAT", "VIN", "PANEL_POS", "3V3", "5V", "12V", "-12V"}


class ElectricalRuleChecker:
    """Small first-pass ERC system with explicit, visible diagnostics."""

    def __init__(self, library: ComponentLibrary) -> None:
        self.library = library

    def check(self, circuit: Circuit) -> list[Diagnostic]:
        diagnostics: list[Diagnostic] = []
        ref_to_id = {component.ref: component.component_id for component in circuit.components}
        pin_to_net: dict[tuple[str, str], str] = {}
        net_pins: dict[str, list[tuple[str, str, ElectricalType]]] = defaultdict(list)
        for net in circuit.nets:
            if len(net.pins) == 1 and not net.allow_single:
                diagnostics.append(Diagnostic(severity=Severity.WARNING, message=f"Net {net.name} has only one connection"))
            for pinref in net.pins:
                definition = self.library.get(ref_to_id.get(pinref.component_ref, ""))
                pin = definition.resolve_pin(pinref.pin_name) if definition else None
                if not pin:
                    continue
                key = (pinref.component_ref, pin.number)
                if key in pin_to_net and pin_to_net[key] != net.name:
                    diagnostics.append(Diagnostic(severity=Severity.ERROR, message=f"Multiple nets assigned to {pinref.component_ref}.{pin.name}"))
                pin_to_net[key] = net.name
                net_pins[net.name].append((pinref.component_ref, pin.name, pin.electrical_type))

        for net_name, pins in net_pins.items():
            outputs = [p for p in pins if p[2] == ElectricalType.output]
            power_outputs = [p for p in pins if p[2] == ElectricalType.power_out]
            power_inputs = [p for p in pins if p[2] == ElectricalType.power_in]
            if len(outputs) > 1:
                diagnostics.append(Diagnostic(severity=Severity.ERROR, code="ERC_OUTPUT_CONFLICT", message=f"Two push-pull outputs connected on {net_name}", net_name=net_name))
            if len(power_outputs) > 1:
                diagnostics.append(Diagnostic(severity=Severity.ERROR, code="ERC_POWER_OUTPUT_CONFLICT", message=f"Multiple power outputs connected on {net_name}", net_name=net_name))
            if power_inputs and not power_outputs and net_name not in (GROUND_NETS | SOURCE_LIKE_NETS):
                diagnostics.append(Diagnostic(severity=Severity.WARNING, code="ERC_POWER_INPUT_NO_SOURCE", message=f"Power-input pin on {net_name} has no apparent source", net_name=net_name))

        self._required_pin_checks(circuit, ref_to_id, pin_to_net, diagnostics)
        self._led_current_limit_check(circuit, ref_to_id, diagnostics)
        self._mosfet_gate_check(circuit, ref_to_id, pin_to_net, diagnostics)
        self._mosfet_pull_down_check(circuit, ref_to_id, pin_to_net, diagnostics)
        return diagnostics

    def _required_pin_checks(self, circuit: Circuit, ref_to_id: dict[str, str], pin_to_net: dict[tuple[str, str], str], diagnostics: list[Diagnostic]) -> None:
        for component in circuit.components:
            definition = self.library.get(ref_to_id[component.ref])
            if not definition:
                continue
            for pin in definition.pins:
                if pin.electrical_type == ElectricalType.no_connect or not pin.required:
                    continue
                if (component.ref, pin.number) not in pin_to_net and pin.name in {"GND", "VDD", "VCC"}:
                    diagnostics.append(Diagnostic(severity=Severity.ERROR, code="ERC_REQUIRED_PIN_UNCONNECTED", message=f"{component.ref}.{pin.name} is not connected", component_ref=component.ref, pin_ref=pin.name))

    def _led_current_limit_check(self, circuit: Circuit, ref_to_id: dict[str, str], diagnostics: list[Diagnostic]) -> None:
        resistive_nets = {net.name for net in circuit.nets for pin in net.pins if ref_to_id.get(pin.component_ref) == "BASIC_RESISTOR"}
        pin_nets = self._pin_name_to_net(circuit, ref_to_id)
        for component in circuit.components:
            if component.component_id.startswith("LIGHT_LED_") and component.component_id != "LIGHT_LED_RGB_ADDRESSABLE" and component.parameters.get("internally_regulated") != "true":
                led_nets = {net.name for net in circuit.nets for pin in net.pins if pin.component_ref == component.ref}
                if not led_nets & resistive_nets:
                    diagnostics.append(Diagnostic(severity=Severity.WARNING, code="ERC_LED_NO_CURRENT_LIMIT", message=f"{component.ref} LED appears to lack a current-limiting resistor", component_ref=component.ref))
                anode_net = pin_nets.get((component.ref, "A"))
                cathode_net = pin_nets.get((component.ref, "K"))
                if anode_net in GROUND_NETS or cathode_net in SOURCE_LIKE_NETS:
                    diagnostics.append(Diagnostic(severity=Severity.WARNING, code="ERC_LED_POLARITY_REVERSED", message=f"{component.ref} LED polarity appears reversed", component_ref=component.ref, metadata={"anode_net": anode_net, "cathode_net": cathode_net}))

    def _mosfet_gate_check(self, circuit: Circuit, ref_to_id: dict[str, str], pin_to_net: dict[tuple[str, str], str], diagnostics: list[Diagnostic]) -> None:
        for component in circuit.components:
            if component.component_id in {"BASIC_NMOS", "BASIC_PMOS"}:
                definition = self.library.get(component.component_id)
                gate = definition.resolve_pin("G") if definition else None
                if gate and (component.ref, gate.number) not in pin_to_net:
                    diagnostics.append(Diagnostic(severity=Severity.ERROR, code="ERC_MOSFET_GATE_FLOATING", message=f"{component.ref} MOSFET gate is floating", component_ref=component.ref, pin_ref="G"))

    def _mosfet_pull_down_check(self, circuit: Circuit, ref_to_id: dict[str, str], pin_to_net: dict[tuple[str, str], str], diagnostics: list[Diagnostic]) -> None:
        pin_nets = self._pin_name_to_net(circuit, ref_to_id)
        resistor_refs = [component.ref for component in circuit.components if component.component_id == "BASIC_RESISTOR"]
        resistor_nets = {
            ref: {net.name for net in circuit.nets for pin in net.pins if pin.component_ref == ref}
            for ref in resistor_refs
        }
        for component in circuit.components:
            if component.component_id != "BASIC_NMOS":
                continue
            gate_net = pin_nets.get((component.ref, "G"))
            source_net = pin_nets.get((component.ref, "S"))
            if source_net not in GROUND_NETS:
                diagnostics.append(Diagnostic(severity=Severity.WARNING, code="ERC_MOSFET_SOURCE_NOT_GROUND", message=f"{component.ref} source is not tied to ground", component_ref=component.ref, pin_ref="S", net_name=source_net))
            if not gate_net:
                continue
            has_pulldown = any(gate_net in nets and bool(nets & GROUND_NETS) for nets in resistor_nets.values())
            if not has_pulldown:
                adjacent_nets = set()
                for nets in resistor_nets.values():
                    if gate_net in nets:
                        adjacent_nets |= nets - {gate_net}
                has_pulldown = any(bool(adjacent_nets & nets) and bool(nets & GROUND_NETS) for nets in resistor_nets.values())
            if not has_pulldown:
                diagnostics.append(Diagnostic(severity=Severity.WARNING, code="ERC_MOSFET_GATE_NO_PULLDOWN", message=f"{component.ref} gate net lacks a pull-down resistor", component_ref=component.ref, net_name=gate_net))

    def _pin_name_to_net(self, circuit: Circuit, ref_to_id: dict[str, str]) -> dict[tuple[str, str], str]:
        result: dict[tuple[str, str], str] = {}
        for net in circuit.nets:
            for pinref in net.pins:
                definition = self.library.get(ref_to_id.get(pinref.component_ref, ""))
                pin = definition.resolve_pin(pinref.pin_name) if definition else None
                if pin:
                    result[(pinref.component_ref, pin.name)] = net.name
        return result
