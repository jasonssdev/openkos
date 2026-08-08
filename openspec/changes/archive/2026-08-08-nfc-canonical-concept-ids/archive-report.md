# Archive Report — `nfc-canonical-concept-ids`

**Archived**: 2026-08-08. **Delivered**: PR #472, squash commit `3ec6c41` on
`main`. **Closes**: #430. **Follow-ups**: #473 (open, P3 — route the tenth
reconstruction site, `_stage_filed_answer`, through `okf.concept_path_for`,
and fix the "nine"/seven site count in this change's docs), #474 (open, P3 —
`lint` detection of non-NFC on-disk names and the rename-migration decision,
deferred by design D3).

## What shipped

NFC is the canonical spelling of a concept id, recorded and enforced:
`okf.concept_id_for` NFC-normalizes the id it derives (closing the silent
failures #429 made reachable — dropped graph edges, invented lint orphans,
missed entity-resolution candidates), and the new `okf.concept_path_for`
resolves id→path tolerantly for the seven reconstruction sites — direct probe
first, then segment-by-segment NFC-name matching with two fail-closed guards
(non-symlink-only admission, ASCII segments never scan). Delta specs merged:
new `concept-identity` domain; `graph-projection` "Node Identity Is The OKF
Concept ID" now NFC with the decomposed-target edge-survival scenario.

## Review

Gentle AI receipt-driven review, lineage `review-a8dc95dd69d76768` (high
risk, canonical 4R). The reliability lens caught a CRITICAL in the first cut
— the fallback scanned only the leaf's parent and silently missed decomposed
*ancestor* directories — corroborated by an independent refuter, fixed within
the single bounded correction (segment-wise resolver + pinned test), passed
targeted validation, receipt approved. Two non-blocking WARNINGs became the
follow-ups above.

## Final state at close

- Suite 3897 passed; `ruff check`, `ruff format --check`, `mypy .` all clean;
  branch coverage 97.24% (gate ≥ 90).
- CI green on 3.12/3.13/3.14 plus quality and build jobs; PR #472 squash-merged.
- No ADR: the decision is one-line reversible at a single derivation site and
  rewrites no bundle bytes (design D1).
- Known doc nit shipped deliberately (frozen candidate): proposal/design/tasks
  said "nine" reconstruction sites where the candidate routes seven — tracked
  in #473 and corrected in these archived copies by that fix.
