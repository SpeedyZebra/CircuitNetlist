# Render Quality Modes

QA-4 separates live browser rendering from strict QA validation.

## Modes

- `interactive`: browser default. Uses fast heuristic AUTO route style, disables per-net scene-validated route-style candidate search, and uses placement optimizer mode `off` for normal load/reroute.
- `strict`: QA mode. Enables scene-validated AUTO route-style candidates and normal route-validated placement optimization.
- `audit`: circuit-audit mode. Uses shared visual DRC and a larger route-style candidate validation budget.

## App Cache

The app keeps a lightweight current render cache keyed by source text, source filename, canonical layout JSON, render quality mode, and component-library file signature.

The cache stores the parsed circuit, layout, routes, scene, SVG, diagnostics, topology analysis, and timing metrics. It is invalidated when the circuit, layout, route-style override, or saved layout changes.

## Timing Metrics

Render responses expose timing metrics:

- `parse_time_ms`
- `placement_time_ms`
- `route_time_ms`
- `scene_time_ms`
- `drc_time_ms`
- `svg_time_ms`
- `total_render_time_ms`
- `quality_mode`
- `cache_hit`

Playwright and simulation remain deferred.
