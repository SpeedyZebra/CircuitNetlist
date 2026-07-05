# Route Style Validation

QA-3 upgrades AUTO net route style from estimate-only selection to bounded scene validation.

## Candidate Flow

For prioritized AUTO nets, the router:

1. Builds an initial deterministic route pass.
2. Chooses a bounded set of AUTO nets for validation.
3. Generates candidate styles:
   - `direct` and `label` for normal nets
   - `power_symbol`, `direct`, and `label` for semantic global rails
4. Builds a canonical scene for each candidate.
5. Runs shared `visual_drc_from_scene`.
6. Selects the cleanest readable candidate.
7. Attaches debug metadata to the routed net.

Manual layout overrides are not changed by AUTO validation.

## Bounds

Full combinatorial validation is intentionally avoided. The default router validates at most two AUTO nets per pass, prioritized by:

- semantic global rails
- direct candidates blocked by component geometry
- heuristic label choices
- non-two-endpoint nets
- longer direct estimates

The placement optimizer disables route-style candidate comparison inside its repeated candidate loop. It still routes, builds a scene, and uses the shared visual DRC for candidate acceptance.

## Metadata

AUTO validation records:

- `candidate_validation`
- `candidates_considered`
- `diagnostics_by_candidate`
- `actual_or_estimated_lengths`
- `bend_counts`
- `label_counts`
- `symbol_counts`
- `selected_candidate`
- `initial_heuristic_style`

If every candidate has issues, AUTO chooses the least-bad candidate and emits a routing warning instead of pretending the style is clean.
