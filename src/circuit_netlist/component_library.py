from __future__ import annotations

import difflib
from pathlib import Path
from typing import Iterable

import yaml
from pydantic import ValidationError

from .models import ComponentDefinition, Diagnostic, Severity


class ComponentLibrary:
    """Validated component definition catalog loaded from YAML files."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.components: dict[str, ComponentDefinition] = {}
        self.diagnostics: list[Diagnostic] = []

    def load(self) -> "ComponentLibrary":
        self.components.clear()
        self.diagnostics.clear()
        for path in sorted(self.root.rglob("*.yaml")):
            self._load_file(path)
        return self

    def _load_file(self, path: Path) -> None:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            items = data if isinstance(data, list) else [data]
            if not all(isinstance(item, dict) for item in items):
                raise ValueError("YAML root must be a mapping or a list of mappings")
            for item in items:
                component = ComponentDefinition.model_validate(item)
                component.source_path = path
                if component.id in self.components:
                    first = self.components[component.id].source_path
                    self.diagnostics.append(
                        Diagnostic(
                            severity=Severity.FATAL,
                            message=f"Duplicate component ID {component.id}; first defined in {first}",
                            file=str(path),
                        )
                    )
                    continue
                self.components[component.id] = component
        except (yaml.YAMLError, ValidationError, ValueError) as exc:
            mark = getattr(getattr(exc, "problem_mark", None), "line", None)
            self.diagnostics.append(
                Diagnostic(
                    severity=Severity.FATAL,
                    message=f"Invalid component YAML: {exc}",
                    file=str(path),
                    line=(mark + 1) if isinstance(mark, int) else None,
                )
            )

    def get(self, component_id: str) -> ComponentDefinition | None:
        return self.components.get(component_id)

    def all(self) -> list[ComponentDefinition]:
        return list(self.components.values())

    def by_category(self) -> dict[str, list[ComponentDefinition]]:
        grouped: dict[str, list[ComponentDefinition]] = {}
        for component in self.all():
            grouped.setdefault(component.category, []).append(component)
        return {key: sorted(value, key=lambda c: c.id) for key, value in sorted(grouped.items())}

    def suggest_component(self, component_id: str) -> list[str]:
        return difflib.get_close_matches(component_id, self.components.keys(), n=5, cutoff=0.45)

    def suggest_pin(self, component_id: str, pin_name: str) -> list[str]:
        component = self.get(component_id)
        if not component:
            return []
        return difflib.get_close_matches(pin_name, component.pin_names(), n=8, cutoff=0.35)


def load_component_library(root: Path) -> ComponentLibrary:
    return ComponentLibrary(root).load()
