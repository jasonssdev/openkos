# Design: Type Provenance-Mirror Edges As `derived_from` At Projection Time (Closes #135)

## Technical Approach

Synthesize `relation_type="derived_from"` at graph-projection read time for any untyped body-link edge whose target is a MEMBER of the source doc's decoded `provenance:` frontmatter list. Purely derived (rebuilt from markdown every run), no frontmatter/ingest write, no migration, retroactive on all bundles. `_candidate_edges` already excludes any pair carrying a typed row, so `suggest-relations` exclusion falls out for free. One necessary downstream guard: `contradiction._candidate_pairs` keys on typed edges only, so it must exclude `derived_from` or it would newly feed every concept→source provenance pair to the LLM.

## Architecture Decisions

### Decision: Synthesize at projection, replacing None (not a second row)
| Option | Tradeoff | Decision |
|---|---|---|
| (c-projection) type provenance mirror in `sqlite_graph` untyped pass | edge is truthfully `derived_from` graph-wide; `_candidate_edges` free; needs contradiction guard | **CHOSEN** (proposal's pick) |
| (a) filter in `edge_typing` only, leave graph None | zero blast radius, no contradiction change | Rejected: edge_typing has no frontmatter access (only `Edge`), would need a new per-source read pass; derivation fact captured nowhere |
| (c-frontmatter) write `relations:` at ingest | durable | Rejected: breaks ingest byte-identity golden + needs migration |

The provenance-mirror row REPLACES the `None` it would otherwise insert in the body-link pass — it stays ONE row, so edge count (and PPR/retrieval) is unchanged; only the type attribute flips.

### Decision: Predicate keys on `provenance:` membership, never `related_note` text
`_is_provenance_mirror(source_id, target_id)` ≡ `target_id ∈ provenance_by_source[source_id]`. Keying on frontmatter membership (not a `sources/` prefix) correctly types `query --save`'s concept-cited provenance links AND avoids mistyping them off their misleading `related_note`. Multi-entry `provenance:` is handled by plain set membership for any list length. Pure/deterministic — removes the LLM call for these edges entirely.

### Decision: Guard `contradiction._candidate_pairs` against `derived_from`
A derivation to a source is not a peer contradiction candidate. Excluding `derived_from`-typed edges keeps find-contradictions' candidate set (and cost) unchanged. This is the concrete outcome of the proposal's mandated downstream audit, not scope creep. A design note in this document suffices; no standalone ADR (change is derived-only and revert-restores).

## Data Flow

    okf provenance: frontmatter ──┐
                                   ▼
    body [x](/x.md) link ─→ _populate_graph_tables ─→ Edge(type=derived_from | None)
                                   │                          │
                    edge_typing._candidate_edges         contradiction._candidate_pairs
                    (derived_from excluded → 0 LLM)       (derived_from GUARDED out)

## Downstream Consumer Audit (core deliverable)

| Consumer | file:line | Reads type? | Effect of typing provenance rows | Verdict |
|---|---|---|---|---|
| suggest-relations `untyped_edges`/`_candidate_edges` | edge_typing.py:102-138 | Yes (`is None`) | Now excluded from candidates → no LLM call | **Intended** — the goal |
| find-contradictions `_candidate_pairs` | contradiction.py:149-185 | Yes (`is not None`) | Would ADD every concept→source pair as a candidate (new LLM cost + spurious concept-vs-source) | **Regression — GUARD required** (exclude `derived_from`) |
| find-contradictions `_pair_relation_types` | contradiction.py:188-200 | Yes | Only `.get()` for candidate pairs; guarded pairs never looked up | Benign, no change |
| graph retrieval PPR | graph_retrieve.py, analysis.py:37-38 | No (undirected view, attr ignored) | Same edge set, attr flips None→derived_from, PPR ignores it | Benign |
| bundle references (forget) | references.py:123-170 | N/A — reads files directly, NOT the graph projection | Untouched | Benign |
| adjudication (find candidates) | resolution/adjudication.py | Does not read graph edges | Untouched | Benign |
| CLI `query` persisted graph.db | cli/main.py:5204 | Via PPR only | graph.db content flips type (derived, rebuilt by reindex) | Benign |
| `status`/`lint` | — | No untyped-edge count surfaced | None | Benign |

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/graph/sqlite_graph.py` | Modify | Build `provenance_by_source` from existing `metadatas`; in body-link pass insert `derived_from` for mirrors instead of `None`; update module docstring (~20 LOC) |
| `src/openkos/resolution/contradiction.py` | Modify | Exclude `relation_type == "derived_from"` in `_candidate_pairs`; docstring (~4 LOC) |
| `src/openkos/resolution/edge_typing.py` | None | Confirm exclusion is automatic once type is non-None (test-only) |

## Interfaces / Contracts

Provenance coercion must fail-safe on dirty frontmatter: non-list `provenance` → empty set; non-string entries dropped (mirrors module's degrade-not-crash posture). No public signature changes; `Edge` shape unchanged.

## Testing Strategy (strict TDD — RED first)

| Layer | What to Test | Approach |
|---|---|---|
| Unit graph | provenance-mirror body link → `derived_from`; genuine non-provenance concept→concept link stays `None`; multi-entry provenance all typed; existing `relations:` typed edge unaffected; dangling/dirty provenance degrades | `test_sqlite_graph.py`, doc-writing fixtures with `provenance:` + `## Related` |
| Unit edge_typing | provenance mirror absent from `candidate_edges`; provenance-only bundle → zero candidates / zero `llm.chat` | `test_edge_typing.py` (assert LLM not called) |
| Unit contradiction | `derived_from`-typed provenance pair excluded from `_candidate_pairs`; genuine typed peer pair still included | `test_contradiction.py` |
| CLI | `suggest-relations` on provenance-only bundle → zero-candidate gate, no LLM | `test_suggest_relations.py` |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary.

## Migration / Rollout

No migration. Derived cache (`graph.db`) rebuilt per reindex from markdown; revert of both files restores prior `None` typing on next build.

## Open Questions

- [ ] None blocking. Accepted consequence (per proposal): on today's ingest-only bundles `suggest-relations` returns zero candidates — honest, becomes useful once concept↔concept candidates exist (deferred (b)).
