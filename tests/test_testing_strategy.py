from __future__ import annotations

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_pytest_markers_are_registered() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    markers = "\n".join(config["tool"]["pytest"]["ini_options"]["markers"])
    for marker in ("unit", "integration", "audit", "slow"):
        assert f"{marker}:" in markers


def test_testing_strategy_documents_fast_and_full_commands() -> None:
    strategy = (ROOT / "docs" / "testing_strategy.md").read_text(encoding="utf-8")
    assert 'pytest -m "not slow and not audit" -q' in strategy
    assert "pytest -q" in strategy
    assert "python -m circuit_netlist.regression --all" in strategy
    assert "python -m circuit_netlist.circuit_audit --all" in strategy
    assert "Playwright execution remains deferred" in strategy
    assert "Simulation remains deferred" in strategy
