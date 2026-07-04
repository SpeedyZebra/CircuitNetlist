from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from .models import Diagnostic


@dataclass(frozen=True)
class NormalizedDiagnostic:
    code: str
    severity: str
    message: str
    owner: str
    component_ref: str | None
    pin_ref: str | None
    net_name: str | None
    source: str | None
    subsystem: str


@dataclass(frozen=True)
class DiagnosticComparison:
    expected_codes: list[str]
    actual_codes: list[str]
    missing_expected_codes: list[str]
    unexpected_codes: list[str]
    matched: bool


def normalize_diagnostic(diagnostic: Diagnostic, default_subsystem: str | None = None) -> NormalizedDiagnostic:
    code = diagnostic.code or "UNCLASSIFIED_DIAGNOSTIC"
    component_ref = diagnostic.component_ref
    net_name = diagnostic.net_name
    owner = component_ref or net_name or diagnostic.pin_ref or ""
    return NormalizedDiagnostic(
        code=code,
        severity=diagnostic.severity.value,
        message=diagnostic.message,
        owner=owner,
        component_ref=component_ref,
        pin_ref=diagnostic.pin_ref,
        net_name=net_name,
        source=diagnostic.source_file or diagnostic.file,
        subsystem=diagnostic_subsystem(code, default_subsystem),
    )


def normalize_diagnostics(diagnostics: Iterable[Diagnostic], default_subsystem: str | None = None) -> list[NormalizedDiagnostic]:
    return [normalize_diagnostic(diagnostic, default_subsystem) for diagnostic in diagnostics]


def diagnostic_codes(diagnostics: Iterable[Diagnostic | NormalizedDiagnostic]) -> list[str]:
    codes: list[str] = []
    for diagnostic in diagnostics:
        code = diagnostic.code
        if code:
            codes.append(code)
        else:
            codes.append("UNCLASSIFIED_DIAGNOSTIC")
    return sorted(codes)


def diagnostic_code_counts(diagnostics: Iterable[Diagnostic | NormalizedDiagnostic]) -> Counter[str]:
    return Counter(diagnostic_codes(diagnostics))


def compare_diagnostic_codes(
    expected_codes: list[str],
    actual_codes: list[str],
    allowed_codes: list[str] | None = None,
) -> tuple[list[str], list[str], bool]:
    comparison = compare_expected_diagnostics(expected_codes, actual_codes, allowed_codes)
    return comparison.missing_expected_codes, comparison.unexpected_codes, comparison.matched


def compare_expected_diagnostics(
    expected_codes: list[str],
    actual_codes: list[str],
    allowed_codes: list[str] | None = None,
) -> DiagnosticComparison:
    """Compare diagnostic code multisets without suppressing namespaces globally."""
    allowed = set(allowed_codes or [])
    expected_counts = Counter(expected_codes)
    actual_counts = Counter(code for code in actual_codes if code not in allowed)
    missing: list[str] = []
    unexpected: list[str] = []
    for code in sorted(set(expected_counts) | set(actual_counts)):
        expected_count = expected_counts.get(code, 0)
        actual_count = actual_counts.get(code, 0)
        if actual_count < expected_count:
            missing.extend([code] * (expected_count - actual_count))
        if actual_count > expected_count:
            unexpected.extend([code] * (actual_count - expected_count))
    return DiagnosticComparison(
        expected_codes=list(expected_codes),
        actual_codes=sorted(actual_codes),
        missing_expected_codes=missing,
        unexpected_codes=unexpected,
        matched=not missing and not unexpected,
    )


def diagnostic_subsystem(code: str, default_subsystem: str | None = None) -> str:
    if "_" in code:
        prefix = code.split("_", 1)[0]
        if prefix:
            return prefix
    return default_subsystem or "GENERAL"
