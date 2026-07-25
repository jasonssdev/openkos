# Proposal: Type Provenance-Mirror Edges As derived_from At Projection Time

Closes #135.

## Intent

`suggest-relations` asks the LLM to type body-link edges that merely mirror a concept's `provenance:` frontmatter. On today's ingest-only bundles every concept→source link is a `provenance:` duplicate, so the verb spends one LLM call per edge re-deriving a fact `build_concept` already encoded — non-deterministic work on a known fact. Make provenance-mirror edges `derived_from` deterministically so they stop being suggestion candidates.

## Scope

### In Scope
- A shared predicate `_is_provenance_mirror(edge, source_frontmatter)`: True iff the edge target id is a MEMBER of the source concept's decoded `provenance:` list.
- In `sqlite_graph.py` edge projection (the `_LINK_RE` body-link walk, ~:86, and `Edge` construction): synthesize `relation_type="derived_from"` for matching rows instead of leaving `relation_type=None`.
- Confirm `untyped_edges`/`_candidate_edges` (edge_typing.py) skip these rows with no (or minimal) logic change, since (a) falls out of the now-typed rows.

### Out of Scope (Non-Goals)
- **(b)** Generating concept↔concept CANDIDATE edges (extraction LLM / mining pass / manual). Separate, unbounded follow-up. This change creates NO new candidate edges.
- **(c-frontmatter)** Writing `relations:` at ingest. Rejected: breaks ingest byte-identity golden test + needs migration.
- The companion vocabulary-warning-spam / one-LLM-call-per-edge perf bug. Tracked separately.

## Capabilities

### New Capabilities
- None

### Modified Capabilities
- `graph-projection`: projection MUST synthesize `relation_type="derived_from"` for untyped body-link edges whose target is a member of the source doc's `provenance:` frontmatter list.
- `llm-edge-production`: candidate set MUST exclude provenance-mirror edges (they are now typed, not `NULL`).

## Approach

Compute provenance membership from each doc's decoded `provenance:` frontmatter (keyed on membership only, never `related_note` text). At projection read time, matched rows get `relation_type="derived_from"`; unmatched rows stay `None`. `_candidate_edges` already excludes any pair carrying a typed row, so exclusion falls out for free. Retroactive on every existing bundle — no re-ingest, no migration.

## Accepted Consequence

On today's bundles this makes `suggest-relations` return NOTHING: every edge becomes a typed `derived_from` mirror. This is intended and honest — there is nothing genuine to type until (b) supplies concept↔concept candidates (which `query --save` already produces in shape today). The verb becomes meaningful once (b) lands.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/graph/sqlite_graph.py` | Modified | Synthesize `derived_from` at projection |
| `src/openkos/resolution/edge_typing.py` | Modified (minimal) | Provenance-mirror rows excluded via typing |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Multi-entry `provenance:` semantics | Low | No current caller passes >1; membership test handles any list length; define explicitly in spec |
| Flipping `None`→`derived_from` breaks downstream that assumes provenance rows untyped | Med | Audit `adjudicate`/tests/graph analysis for reliance on `None` before ship |
| Mistyping `query --save` citation edges | Low | Key strictly on `provenance:` membership, never `related_note` text |

## Rollback Plan

Revert the `sqlite_graph.py` projection change (and any `edge_typing.py` tweak). Projection is a read-only derived cache rebuilt per run — reverting restores prior `None` typing on next build with no data migration.

## Dependencies

- `derived_from` already seeded in `SEEDED_RELATION_TYPES` (`relations.py:32`). No vocabulary change.

## Success Criteria

- [ ] Provenance-mirror body-link edges project as `relation_type="derived_from"`.
- [ ] `suggest-relations`/`_candidate_edges` no longer surface those edges.
- [ ] Non-provenance untyped edges still surface unchanged.
- [ ] No ingest golden test change; retroactive on existing bundles.
- [ ] `derived_from` determinism win: no LLM call on provenance-mirror edges.
