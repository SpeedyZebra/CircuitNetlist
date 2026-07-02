from __future__ import annotations

import networkx as nx

from .component_library import ComponentLibrary
from .models import Circuit, Layout


def build_circuit_graph(circuit: Circuit, library: ComponentLibrary, layout: Layout | None = None) -> nx.Graph:
    """Build a graph with independently addressable component, pin, and net nodes."""
    graph = nx.Graph()
    ref_to_id = {component.ref: component.component_id for component in circuit.components}
    for component in circuit.components:
        placement = layout.components.get(component.ref) if layout else None
        graph.add_node(f"component:{component.ref}", kind="component", ref=component.ref, component_id=component.component_id, placement=placement)
        definition = library.get(component.component_id)
        if not definition:
            continue
        for pin in definition.pins:
            pin_node = f"pin:{component.ref}:{pin.number}"
            graph.add_node(pin_node, kind="pin", ref=component.ref, pin=pin.model_dump())
            graph.add_edge(f"component:{component.ref}", pin_node, kind="owns")
    for net in circuit.nets:
        net_node = f"net:{net.name}"
        graph.add_node(net_node, kind="net", name=net.name)
        for pinref in net.pins:
            definition = library.get(ref_to_id.get(pinref.component_ref, ""))
            pin = definition.resolve_pin(pinref.pin_name) if definition else None
            if pin:
                graph.add_edge(net_node, f"pin:{pinref.component_ref}:{pin.number}", kind="connects")
    return graph
