from __future__ import annotations

from collections import defaultdict

from .component_library import ComponentLibrary
from .models import Circuit, Diagnostic, EngineeringValue, Severity
from .topology import is_supported_role_text


SUPPLY_NAMES = {"VBAT", "VCC", "VDD", "VIN", "PANEL_POS", "SYS", "+5V", "+3V3"}


class CircuitValidator:
    """Semantic validation that resolves component and pin references."""

    def __init__(self, library: ComponentLibrary) -> None:
        self.library = library

    def validate(self, circuit: Circuit) -> list[Diagnostic]:
        diagnostics: list[Diagnostic] = [*self.library.diagnostics]
        refs: dict[str, str] = {}
        for component in circuit.components:
            source = str(circuit.source_path) if circuit.source_path else None
            if component.ref in refs:
                diagnostics.append(Diagnostic(severity=Severity.ERROR, code="VALIDATION_DUPLICATE_REF", message=f"Duplicate reference designator: {component.ref}", component_ref=component.ref, file=source, source_file=source, line=component.line))
            refs[component.ref] = component.component_id
            definition = self.library.get(component.component_id)
            if not definition:
                diagnostics.append(
                    Diagnostic(
                        severity=Severity.ERROR,
                        code="VALIDATION_UNKNOWN_COMPONENT",
                        message=f"Unknown component type: {component.component_id}",
                        component_ref=component.ref,
                        file=source,
                        source_file=source,
                        line=component.line,
                        suggestions=self.library.suggest_component(component.component_id),
                    )
                )
                continue
            allowed = set(definition.parameters)
            required = {name for name, spec in definition.parameters.items() if spec.get("required")}
            supplied = set(component.parameters)
            for param in sorted(required - supplied):
                diagnostics.append(Diagnostic(severity=Severity.ERROR, code="VALIDATION_MISSING_PARAMETER", message=f"{component.ref} missing required parameter: {param}", component_ref=component.ref, file=source, source_file=source, line=component.line))
            for param in sorted(supplied - allowed):
                diagnostics.append(Diagnostic(severity=Severity.ERROR, code="VALIDATION_UNKNOWN_PARAMETER", message=f"{component.ref} unknown parameter: {param}", component_ref=component.ref, file=source, source_file=source, line=component.line, metadata={"parameter": param}))
            for name, value in component.parameters.items():
                if isinstance(value, EngineeringValue) and value.numeric is None and name in allowed:
                    diagnostics.append(Diagnostic(severity=Severity.ERROR, code="VALIDATION_BAD_VALUE", message=f"{component.ref}.{name} has unsupported value format: {value.original}", component_ref=component.ref, file=source, source_file=source, line=component.line, metadata={"parameter": name, "value": value.original}))
                if name == "role":
                    role_text = str(getattr(value, "original", value))
                    if not is_supported_role_text(role_text):
                        diagnostics.append(Diagnostic(severity=Severity.WARNING, code="VALIDATION_UNKNOWN_ROLE", message=f"{component.ref} has unsupported role intent: {role_text}", component_ref=component.ref, file=source, source_file=source, line=component.line, metadata={"role": role_text}))

        pin_assignments: dict[tuple[str, str], str] = {}
        for net in circuit.nets:
            if len(net.pins) < 2 and not net.allow_single:
                diagnostics.append(Diagnostic(severity=Severity.ERROR, code="VALIDATION_SINGLE_PIN_NET", message=f"Net {net.name} has only one connection", net_name=net.name, file=str(circuit.source_path) if circuit.source_path else None, source_file=str(circuit.source_path) if circuit.source_path else None, line=net.line))
            for pinref in net.pins:
                component_id = refs.get(pinref.component_ref)
                definition = self.library.get(component_id) if component_id else None
                if not definition:
                    diagnostics.append(Diagnostic(severity=Severity.ERROR, code="VALIDATION_UNKNOWN_REF", message=f"Unknown component reference: {pinref.component_ref}", component_ref=pinref.component_ref, net_name=net.name, file=str(circuit.source_path) if circuit.source_path else None, source_file=str(circuit.source_path) if circuit.source_path else None, line=pinref.line))
                    continue
                pin = definition.resolve_pin(pinref.pin_name)
                if not pin:
                    diagnostics.append(
                        Diagnostic(
                            severity=Severity.ERROR,
                            code="VALIDATION_UNKNOWN_PIN",
                            message=f"{pinref.component_ref}.{pinref.pin_name} does not exist.",
                            component_ref=pinref.component_ref,
                            pin_ref=pinref.pin_name,
                            net_name=net.name,
                            file=str(circuit.source_path) if circuit.source_path else None,
                            source_file=str(circuit.source_path) if circuit.source_path else None,
                            line=pinref.line,
                            suggestions=definition.pin_names(),
                        )
                    )
                    continue
                pinref.resolved_number = pin.number
                pinref.resolved_name = pin.name
                key = (pinref.component_ref, pin.number)
                if key in pin_assignments and pin_assignments[key] != net.name:
                    diagnostics.append(Diagnostic(severity=Severity.ERROR, code="VALIDATION_PIN_MULTIPLE_NETS", message=f"{pinref.component_ref}.{pin.name} assigned to both {pin_assignments[key]} and {net.name}", component_ref=pinref.component_ref, pin_ref=pin.name, net_name=net.name, file=str(circuit.source_path) if circuit.source_path else None, source_file=str(circuit.source_path) if circuit.source_path else None, line=pinref.line))
                pin_assignments[key] = net.name

        net_names = {net.name for net in circuit.nets}
        if "GND" in net_names:
            for rail in sorted(SUPPLY_NAMES & net_names):
                if rail == "GND":
                    continue
                if self._share_component_terminal(circuit, rail, "GND"):
                    diagnostics.append(Diagnostic(severity=Severity.ERROR, code="ERC_POWER_GND_SHORT", message=f"{rail} is directly shorted to GND", net_name=rail))
        self._check_terminal_shorts(circuit, diagnostics)
        return diagnostics

    def _share_component_terminal(self, circuit: Circuit, a: str, b: str) -> bool:
        net_map = {net.name: {(pin.component_ref, pin.resolved_number or pin.pin_name) for pin in net.pins} for net in circuit.nets}
        return bool(net_map.get(a, set()) & net_map.get(b, set()))

    def _check_terminal_shorts(self, circuit: Circuit, diagnostics: list[Diagnostic]) -> None:
        by_ref: dict[str, dict[str, str]] = defaultdict(dict)
        for net in circuit.nets:
            for pin in net.pins:
                by_ref[pin.component_ref][pin.resolved_name or pin.pin_name] = net.name
        for component in circuit.components:
            pins = by_ref.get(component.ref, {})
            if component.component_id.startswith("LIGHT_LED_") and pins.get("A") and pins.get("A") == pins.get("K"):
                diagnostics.append(Diagnostic(severity=Severity.ERROR, code="ERC_LED_TERMINALS_SHORTED", message=f"{component.ref} LED terminals are shorted together", component_ref=component.ref, net_name=pins.get("A")))
            if component.component_id.startswith(("POWER_LIPO", "POWER_LIION")) and pins.get("POS") and pins.get("POS") == pins.get("NEG"):
                diagnostics.append(Diagnostic(severity=Severity.ERROR, code="ERC_BATTERY_TERMINALS_SHORTED", message=f"{component.ref} battery terminals are shorted together", component_ref=component.ref, net_name=pins.get("POS")))
            if component.component_id.startswith("POWER_SOLAR") and pins.get("POS") and pins.get("POS") == pins.get("NEG"):
                diagnostics.append(Diagnostic(severity=Severity.ERROR, code="ERC_SOLAR_TERMINALS_SHORTED", message=f"{component.ref} solar terminals are shorted together", component_ref=component.ref, net_name=pins.get("POS")))


def has_blocking_diagnostics(diagnostics: list[Diagnostic]) -> bool:
    return any(diag.severity in {Severity.ERROR, Severity.FATAL} for diag in diagnostics)
