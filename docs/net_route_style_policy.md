# Net Route Style Policy

QA-2 adds an explicit route-style model for each net:

- `auto`
- `direct`
- `label`
- `power_symbol`

The route style never changes electrical connectivity. It only controls how a net is drawn.

## Resolution Order

1. Manual layout override from `layout.nets[net_name].route_style`
2. Legacy `render_style` layout compatibility
3. Semantic global-net classification
4. Deterministic AUTO heuristic

Manual overrides are saved in layout JSON. Setting a net back to `auto` removes the override from the layout entry.

## AUTO Heuristic

AUTO chooses `power_symbol` for global rails such as `GND`, `VCC`, `VDD`, `VSS`, `VBAT`, `3V3`, `5V`, signed rails, and `V+`/`V-`.

AUTO chooses direct wires for close, simple two-endpoint nets and local three-endpoint nets when the direct candidate is short, low-bend, and does not cross component obstacles in the router's deterministic estimate.

AUTO chooses labels for distant nets, fanout nets, repeated-channel/sense/control nets, and direct candidates blocked by component geometry.

## Debug Metadata

Every routed net records route-style metadata:

- `selected_style`
- `manual_override`
- `reason`
- `endpoint_count`
- `estimated_direct_length`
- `estimated_direct_bends`
- `direct_drc_status`
- `label_drc_status`

The frontend exposes these fields in the net properties panel.

## UI Control

Selecting a wire, net label, power symbol, or net group shows a route-style dropdown. Changing the dropdown posts to `POST /api/layout/net-route-style`, preserves component placement, rerenders the scene, and marks the layout dirty.
