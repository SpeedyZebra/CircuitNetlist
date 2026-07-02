from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PinSide = Literal["left", "right", "top", "bottom"]


class ElectricalType(str, Enum):
    power_in = "power_in"
    power_out = "power_out"
    input = "input"
    output = "output"
    bidirectional = "bidirectional"
    passive = "passive"
    open_drain = "open_drain"
    no_connect = "no_connect"


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    FATAL = "FATAL"


class Diagnostic(BaseModel):
    severity: Severity
    message: str
    code: str | None = None
    component_ref: str | None = None
    pin_ref: str | None = None
    net_name: str | None = None
    source_file: str | None = None
    file: str | None = None
    line: int | None = None
    column: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    suggestions: list[str] = Field(default_factory=list)


class BodyDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    renderer: str
    width: int = 120
    height: int = 80
    fill: str = "#f4f4f4"
    stroke: str = "#222222"
    notch: str | None = None
    pin_pitch: int = 32


class PinDefinition(BaseModel):
    number: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    side: PinSide
    position: int = Field(ge=1)
    electrical_type: ElectricalType
    required: bool = True


class ComponentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    category: str
    manufacturer: str = ""
    part_number: str = ""
    package: str = ""
    description: str = ""
    body: BodyDefinition
    pins: list[PinDefinition]
    parameters: dict[str, dict[str, Any]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_path: Path | None = None

    @model_validator(mode="after")
    def reject_duplicate_pin_numbers(self) -> "ComponentDefinition":
        numbers = [pin.number for pin in self.pins]
        if len(numbers) != len(set(numbers)):
            raise ValueError("duplicate physical pin numbers")
        return self

    def resolve_pin(self, token: str) -> PinDefinition | None:
        for pin in self.pins:
            names = {pin.number, pin.name, *pin.aliases}
            if token in names:
                return pin
        return None

    def pin_names(self) -> list[str]:
        names: list[str] = []
        for pin in self.pins:
            names.extend([pin.name, pin.number, *pin.aliases])
        return sorted(set(names))


class EngineeringValue(BaseModel):
    original: str
    numeric: float | None = None
    unit: str | None = None


class ComponentInstance(BaseModel):
    ref: str
    component_id: str
    parameters: dict[str, EngineeringValue | str] = Field(default_factory=dict)
    line: int = 0


class PinRef(BaseModel):
    component_ref: str
    pin_name: str
    line: int = 0
    resolved_number: str | None = None
    resolved_name: str | None = None


class Net(BaseModel):
    name: str
    pins: list[PinRef]
    allow_single: bool = False
    line: int = 0


class Circuit(BaseModel):
    name: str
    components: list[ComponentInstance]
    nets: list[Net]
    source_path: Path | None = None


class Placement(BaseModel):
    x: int
    y: int
    rotation: int = 0
    locked: bool = False


class Layout(BaseModel):
    components: dict[str, Placement] = Field(default_factory=dict)
    nets: dict[str, dict[str, Any]] = Field(default_factory=dict)
    wire_waypoints: dict[str, list[tuple[int, int]]] = Field(default_factory=dict)
    canvas: dict[str, Any] = Field(default_factory=lambda: {"grid": 20, "width": 1600, "height": 1000})


class RoutedNet(BaseModel):
    name: str
    render_style: Literal["local_wire", "net_label", "power_symbol"] = "local_wire"
    segments: list[tuple[int, int, int, int]] = Field(default_factory=list)
    labels: list[tuple[int, int, str]] = Field(default_factory=list)
    endpoints: list[dict[str, Any]] = Field(default_factory=list)
    junctions: list[tuple[int, int]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RenderedCircuit(BaseModel):
    svg: str
    diagnostics: list[Diagnostic]
    layout: Layout
    circuit: Circuit | None = None
