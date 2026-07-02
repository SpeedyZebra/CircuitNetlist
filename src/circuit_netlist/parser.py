from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from lark import Lark, Token, Transformer, UnexpectedInput, v_args

from .models import Circuit, ComponentInstance, Diagnostic, EngineeringValue, Net, PinRef, Severity


GRAMMAR = r"""
start: circuit component* net*
circuit: "CIRCUIT" NAME
component: "COMPONENT" REF NAME param*
net: "NET" NAME net_option? ":" pinref+
net_option: "[" "allow_single" "]"
pinref: REF "." PIN
param: NAME "=" value
value: ESCAPED_STRING -> string
     | VALUE          -> bare

REF: /[A-Z][A-Za-z0-9_]*/
PIN: /[A-Za-z0-9_+\-]+/
NAME: /[A-Za-z_][A-Za-z0-9_]*/
VALUE: /"[^"]*"|[^\s#]+/
COMMENT: /#[^\n]*/
%import common.ESCAPED_STRING
%import common.WS
%ignore WS
%ignore COMMENT
"""


VALUE_RE = re.compile(r"^(?P<num>[+-]?(?:\d+(?:\.\d*)?|\.\d+))(?P<suffix>[pnumkM]?)(?P<unit>ohm|mAh|Ah|V|A|W|F|H)?$")
SUFFIXES = {"": 1.0, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "M": 1e6}


def parse_engineering_value(text: str) -> EngineeringValue:
    """Parse a display value into a normalized number where possible."""
    original = text.strip().strip('"')
    match = VALUE_RE.match(original)
    if not match:
        return EngineeringValue(original=original)
    numeric = float(match.group("num")) * SUFFIXES[match.group("suffix")]
    return EngineeringValue(original=original, numeric=numeric, unit=match.group("unit"))


@v_args(meta=True)
class NetlistTransformer(Transformer):
    def start(self, meta: Any, items: list[Any]) -> Circuit:
        name = items[0]
        components = [item for item in items[1:] if isinstance(item, ComponentInstance)]
        nets = [item for item in items[1:] if isinstance(item, Net)]
        return Circuit(name=name, components=components, nets=nets)

    def circuit(self, meta: Any, items: list[Any]) -> str:
        return str(items[0])

    def component(self, meta: Any, items: list[Any]) -> ComponentInstance:
        ref = str(items[0])
        component_id = str(items[1])
        params = dict(items[2:])
        return ComponentInstance(ref=ref, component_id=component_id, parameters=params, line=meta.line)

    def net(self, meta: Any, items: list[Any]) -> Net:
        name = str(items[0])
        allow_single = any(item == "allow_single" for item in items)
        pins = [item for item in items[1:] if isinstance(item, PinRef)]
        return Net(name=name, pins=pins, allow_single=allow_single, line=meta.line)

    def net_option(self, meta: Any, items: list[Any]) -> str:
        return "allow_single"

    def pinref(self, meta: Any, items: list[Any]) -> PinRef:
        return PinRef(component_ref=str(items[0]), pin_name=str(items[1]), line=meta.line)

    def param(self, meta: Any, items: list[Any]) -> tuple[str, EngineeringValue | str]:
        value = items[1]
        if isinstance(value, str):
            parsed = parse_engineering_value(value)
            return (str(items[0]), parsed if parsed.numeric is not None or parsed.unit else value.strip('"'))
        return (str(items[0]), value)

    def string(self, meta: Any, items: list[Any]) -> str:
        token = str(items[0])
        return token[1:-1]

    def bare(self, meta: Any, items: list[Any]) -> str:
        return str(items[0])

    def NAME(self, token: Token) -> str:
        return str(token)

    def REF(self, token: Token) -> str:
        return str(token)

    def PIN(self, token: Token) -> str:
        return str(token)


class NetlistParser:
    """Lark-backed parser for .cnet files."""

    def __init__(self) -> None:
        self._parser = Lark(GRAMMAR, parser="lalr", propagate_positions=True, maybe_placeholders=False)

    def parse_text(self, text: str, source_path: Path | None = None) -> tuple[Circuit | None, list[Diagnostic]]:
        try:
            tree = self._parser.parse(text)
            circuit = NetlistTransformer().transform(tree)
            circuit.source_path = source_path
            return circuit, []
        except UnexpectedInput as exc:
            return None, [
                Diagnostic(
                    severity=Severity.FATAL,
                    code="PARSE_SYNTAX_ERROR",
                    message=f"Parser error: {exc.get_context(text).strip()}",
                    file=str(source_path) if source_path else None,
                    source_file=str(source_path) if source_path else None,
                    line=exc.line,
                    column=exc.column,
                )
            ]

    def parse_file(self, path: Path) -> tuple[Circuit | None, list[Diagnostic]]:
        return self.parse_text(path.read_text(encoding="utf-8"), path)
