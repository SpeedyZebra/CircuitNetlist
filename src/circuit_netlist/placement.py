from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .component_library import ComponentLibrary
from .geometry import component_size
from .models import Circuit, Layout, Placement
from .topology import ComponentRole, MIN_PLACEMENT_PATTERN_CONFIDENCE, PatternType, TopologyAnalysis, TopologyAnalyzer, TopologyPattern

MIN_COMPONENT_GAP = 40
MCU_CLEARANCE = 60


class PlacementEngine(Protocol):
    def place(self, circuit: Circuit, library: ComponentLibrary, existing: Layout | None = None) -> Layout:
        """Return component coordinates without changing electrical connectivity."""


@dataclass
class DeterministicPlacementEngine:
    grid: int = 40
    x_gap: int = 260
    y_gap: int = 170

    def place(self, circuit: Circuit, library: ComponentLibrary, existing: Layout | None = None) -> Layout:
        layout = existing.model_copy(deep=True) if existing else Layout()
        self._existing_refs = set(layout.components)
        layout.canvas["grid"] = self.grid
        analysis = TopologyAnalyzer().analyze(circuit, library)
        self._place_by_role(circuit, library, layout, analysis)
        self._place_patterns(circuit, library, layout, analysis)
        self._choose_two_pin_orientations(circuit, layout, analysis)
        self._fill_unplaced(circuit, library, layout, analysis)
        self._resize_canvas(circuit, library, layout)
        return layout

    def _place_by_role(self, circuit: Circuit, library: ComponentLibrary, layout: Layout, analysis: TopologyAnalysis) -> None:
        columns: dict[str, list[str]] = {"source": [], "power": [], "storage": [], "controller": [], "switch": [], "load": [], "passive": []}
        for component in circuit.components:
            if component.ref in layout.components:
                continue
            role = analysis.component_roles.get(component.ref, ComponentRole.UNKNOWN)
            column = self._role_bucket(component.component_id, role)
            columns[column].append(component.ref)
        x_for = {"source": 80, "power": 240, "storage": 460, "controller": 640, "switch": 1060, "load": 900, "passive": 780}
        for column, refs in columns.items():
            refs.sort()
            y = 140
            if column in {"power", "controller", "switch"}:
                y = 420
            if column == "passive":
                y = 760
            for ref in refs:
                layout.components[ref] = Placement(x=self._snap(x_for[column]), y=self._snap(y))
                instance = next(component for component in circuit.components if component.ref == ref)
                definition = library.get(instance.component_id)
                height = component_size(definition, layout.components[ref])[1] if definition else 100
                y += height + self.y_gap

    def _place_patterns(self, circuit: Circuit, library: ComponentLibrary, layout: Layout, analysis: TopologyAnalysis) -> None:
        self._place_led_current_limits(layout, analysis)
        self._place_gate_resistors(layout, analysis)
        self._place_low_side_switches(circuit, library, layout, analysis)
        self._place_voltage_dividers(circuit, layout, analysis)
        self._place_pull_resistors(layout, analysis)
        self._place_decoupling(circuit, library, layout, analysis)
        self._place_rc_filters(layout, analysis)

    def _place_led_current_limits(self, layout: Layout, analysis: TopologyAnalysis) -> None:
        for pattern in self._strong_patterns(analysis, PatternType.LED_CURRENT_LIMIT):
            resistor = pattern.metadata.get("resistor")
            led = pattern.metadata.get("led")
            if isinstance(resistor, str):
                self._set(layout, resistor, 760, 300, 0)
            if isinstance(led, str):
                self._set(layout, led, 960, 300, 0)

    def _place_gate_resistors(self, layout: Layout, analysis: TopologyAnalysis) -> None:
        for pattern in self._strong_patterns(analysis, PatternType.SERIES_GATE_RESISTOR):
            driver = pattern.metadata.get("driver_component")
            resistor = pattern.metadata.get("gate_resistor")
            mosfet = pattern.metadata.get("mosfet")
            if isinstance(driver, str):
                self._set(layout, driver, 520, 400, 0)
            if isinstance(resistor, str):
                self._set(layout, resistor, 960, 520, 0)
            if isinstance(mosfet, str):
                self._set(layout, mosfet, 1160, 400, 0)

    def _place_low_side_switches(self, circuit: Circuit, library: ComponentLibrary, layout: Layout, analysis: TopologyAnalysis) -> None:
        for pattern in self._strong_patterns(analysis, PatternType.LOW_SIDE_MOSFET_SWITCH):
            mosfet = str(pattern.metadata.get("mosfet", ""))
            if not mosfet or self._is_locked(layout, mosfet):
                continue
            self._set(layout, mosfet, 1160, 400, 0)
            gate_resistor = pattern.metadata.get("gate_resistor")
            if isinstance(gate_resistor, str):
                self._set(layout, gate_resistor, 960, 520, 0)
            pull_down = pattern.metadata.get("pull_down")
            if isinstance(pull_down, str):
                self._set(layout, pull_down, 960, 700, 90)
            load_pattern = pattern.metadata.get("load_pattern")
            if isinstance(load_pattern, dict):
                resistor = load_pattern.get("metadata", {}).get("resistor")
                led = load_pattern.get("metadata", {}).get("led")
                if isinstance(resistor, str):
                    self._set(layout, resistor, 760, 300, 0)
                if isinstance(led, str):
                    self._set(layout, led, 960, 300, 0)
            driver = next((ref for ref in pattern.component_refs if analysis.component_roles.get(ref) == ComponentRole.CONTROLLER), None)
            if driver:
                self._set(layout, driver, 520, 400, 0)

    def _place_voltage_dividers(self, circuit: Circuit, layout: Layout, analysis: TopologyAnalysis) -> None:
        patterns = self._strong_patterns(analysis, PatternType.VOLTAGE_DIVIDER)
        for index, pattern in enumerate(patterns):
            x = 1320 + index * 180
            top = pattern.metadata.get("top_resistor")
            bottom = pattern.metadata.get("bottom_resistor")
            if isinstance(top, str):
                self._set(layout, top, x, 720, 90)
            if isinstance(bottom, str):
                self._set(layout, bottom, x, 960, 90)

    def _place_pull_resistors(self, layout: Layout, analysis: TopologyAnalysis) -> None:
        occupied = 0
        for pattern in [*self._strong_patterns(analysis, PatternType.PULL_DOWN), *self._strong_patterns(analysis, PatternType.PULL_UP)]:
            resistor = pattern.metadata.get("resistor")
            if not isinstance(resistor, str):
                continue
            if pattern.pattern_type == PatternType.PULL_DOWN:
                self._set(layout, resistor, 800 + occupied * 160, 560, 90)
            else:
                self._set(layout, resistor, 800 + occupied * 160, 300, 90)
            occupied += 1

    def _place_decoupling(self, circuit: Circuit, library: ComponentLibrary, layout: Layout, analysis: TopologyAnalysis) -> None:
        for pattern in self._strong_patterns(analysis, PatternType.DECOUPLING_CAPACITOR):
            capacitor = pattern.metadata.get("capacitor")
            target = pattern.metadata.get("target_component")
            if not isinstance(capacitor, str):
                continue
            if isinstance(target, str) and target in layout.components:
                target_placement = layout.components[target]
                target_instance = next((component for component in circuit.components if component.ref == target), None)
                target_definition = library.get(target_instance.component_id) if target_instance else None
                target_height = component_size(target_definition, target_placement)[1] if target_definition else 160
                self._set(layout, capacitor, target_placement.x + 40, target_placement.y + target_height + 240, 90)
            else:
                placement = layout.components.get(capacitor)
                if placement and not placement.locked:
                    placement.rotation = 90

    def _place_rc_filters(self, layout: Layout, analysis: TopologyAnalysis) -> None:
        for pattern in self._strong_patterns(analysis, PatternType.RC_LOWPASS):
            resistor = pattern.metadata.get("resistor")
            capacitor = pattern.metadata.get("capacitor")
            if isinstance(resistor, str):
                self._set(layout, resistor, layout.components.get(resistor, Placement(x=780, y=640)).x, layout.components.get(resistor, Placement(x=780, y=640)).y, 0)
            if isinstance(capacitor, str):
                base = layout.components.get(resistor, Placement(x=780, y=640)) if isinstance(resistor, str) else Placement(x=780, y=640)
                self._set(layout, capacitor, base.x + 160, base.y + 120, 90)

    def _choose_two_pin_orientations(self, circuit: Circuit, layout: Layout, analysis: TopologyAnalysis) -> None:
        orientation_by_ref: dict[str, int] = {}
        for pattern in analysis.patterns:
            if pattern.confidence < MIN_PLACEMENT_PATTERN_CONFIDENCE:
                continue
            if pattern.pattern_type in {PatternType.VOLTAGE_DIVIDER, PatternType.PULL_DOWN, PatternType.PULL_UP, PatternType.DECOUPLING_CAPACITOR}:
                refs = pattern.component_refs
                if pattern.pattern_type == PatternType.DECOUPLING_CAPACITOR:
                    capacitor = pattern.metadata.get("capacitor")
                    refs = [capacitor] if isinstance(capacitor, str) else []
                for ref in refs:
                    orientation_by_ref.setdefault(ref, 90)
            if pattern.pattern_type in {PatternType.SERIES_GATE_RESISTOR, PatternType.LED_CURRENT_LIMIT, PatternType.SERIES_ELEMENT, PatternType.RC_LOWPASS}:
                refs = pattern.component_refs
                if pattern.pattern_type == PatternType.SERIES_GATE_RESISTOR:
                    gate_resistor = pattern.metadata.get("gate_resistor")
                    refs = [gate_resistor] if isinstance(gate_resistor, str) else []
                if pattern.pattern_type == PatternType.LED_CURRENT_LIMIT:
                    resistor = pattern.metadata.get("resistor")
                    refs = [resistor] if isinstance(resistor, str) else []
                if pattern.pattern_type == PatternType.RC_LOWPASS:
                    resistor = pattern.metadata.get("resistor")
                    refs = [resistor] if isinstance(resistor, str) else []
                for ref in refs:
                    orientation_by_ref.setdefault(ref, 0)
            if pattern.pattern_type == PatternType.RC_LOWPASS:
                capacitor = pattern.metadata.get("capacitor")
                if isinstance(capacitor, str):
                    orientation_by_ref[capacitor] = 90
        for component in circuit.components:
            placement = layout.components.get(component.ref)
            if not placement or placement.locked:
                continue
            if component.ref in getattr(self, "_existing_refs", set()):
                continue
            if component.ref in orientation_by_ref:
                placement.rotation = orientation_by_ref[component.ref]

    def _fill_unplaced(self, circuit: Circuit, library: ComponentLibrary, layout: Layout, analysis: TopologyAnalysis) -> None:
        # Kept as a separate hook for later scored placement milestones.
        for component in sorted(circuit.components, key=lambda item: item.ref):
            if component.ref in layout.components:
                continue
            role = analysis.component_roles.get(component.ref, ComponentRole.UNKNOWN)
            column = self._role_bucket(component.component_id, role)
            x_for = {"source": 80, "power": 240, "storage": 460, "controller": 640, "switch": 1060, "load": 900, "passive": 780}
            y = 140 + len(layout.components) * self.y_gap
            layout.components[component.ref] = Placement(x=self._snap(x_for[column]), y=self._snap(y))

    def _role_bucket(self, component_id: str, role: ComponentRole) -> str:
        if role == ComponentRole.SOURCE:
            return "source"
        if role in {ComponentRole.CHARGER, ComponentRole.REGULATOR}:
            return "power"
        if role == ComponentRole.STORAGE:
            return "storage"
        if role == ComponentRole.CONTROLLER:
            return "controller"
        if role == ComponentRole.SWITCH:
            return "switch"
        if role == ComponentRole.LOAD:
            return "load"
        return "passive"

    def _strong_patterns(self, analysis: TopologyAnalysis, pattern_type: PatternType) -> list[TopologyPattern]:
        return [pattern for pattern in analysis.patterns if pattern.pattern_type == pattern_type and pattern.confidence >= MIN_PLACEMENT_PATTERN_CONFIDENCE]

    def _set(self, layout: Layout, ref: str, x: int, y: int, rotation: int) -> None:
        existing = layout.components.get(ref)
        if existing and (existing.locked or ref in getattr(self, "_existing_refs", set())):
            return
        if existing:
            existing.x = self._snap(x)
            existing.y = self._snap(y)
            existing.rotation = rotation
            return
        layout.components[ref] = Placement(x=self._snap(x), y=self._snap(y), rotation=rotation)

    def _is_locked(self, layout: Layout, ref: str) -> bool:
        placement = layout.components.get(ref)
        return bool((placement and placement.locked) or ref in getattr(self, "_existing_refs", set()))

    def _snap(self, value: int) -> int:
        return round(value / self.grid) * self.grid

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
        layout.canvas["width"] = max(int(layout.canvas.get("width", 0)), self._snap(max_x + 220))
        layout.canvas["height"] = max(int(layout.canvas.get("height", 0)), self._snap(max_y + 220))
