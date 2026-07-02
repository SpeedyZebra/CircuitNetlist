from pathlib import Path

from tools.create_source_archive import should_include


def test_source_archive_excludes_generated_and_cache_content() -> None:
    excluded = [
        Path(".git/config"),
        Path("__pycache__/module.pyc"),
        Path(".pytest_cache/v/cache/nodeids"),
        Path("test_circuits/generated/report/index.html"),
        Path("output/debug.svg"),
        Path("dist/CircuitNetlist-source.zip"),
        Path("build/lib/module.py"),
        Path("coverage.xml"),
        Path("src/circuit_netlist.egg-info/PKG-INFO"),
    ]
    assert not [path for path in excluded if should_include(path)]


def test_source_archive_keeps_source_docs_examples_and_tests() -> None:
    included = [
        Path("src/circuit_netlist/parser.py"),
        Path("components/basic.yaml"),
        Path("examples/solar_led.cnet"),
        Path("docs/topology_driven_placement.md"),
        Path("tests/test_topology.py"),
    ]
    assert [path for path in included if should_include(path)] == included
