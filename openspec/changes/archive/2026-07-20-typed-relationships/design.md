# Design: Typed Relationships (Slice 1 — deterministic edges)

## Technical Approach

Add a first-class `relations:` frontmatter key to OKF objects, produced only by a
new deterministic `relate` CLI verb (no LLM this slice), validated by an additive
§9 rule, consumed by `build_graph` to populate `Edge.relation_type`, and protected
by a fail-closed merge guard. Every seam mirrors an existing pattern: the
vocabulary mirrors `types.py::REGISTRY`; `relate` mirrors the `forget` write-verb
scaffold; encode/decode mirror `encode_merged_from`/`decode_merged_from`; the
guard scanner mirrors `links.find_inbound_link_rewrites`. Additive and reversible:
no `relations:` key ⇒ byte-identical to today everywhere.

## Architecture Decisions

### Decision: `relations:` frontmatter shape

| Option | Tradeoff | Decision |
|--------|----------|----------|
| List of `{target, type}` mappings | Structured, queryable, matches `merged_from` list-of-dicts precedent | **Chosen** |
| `{rel, ref}` field names | Novel vocabulary vs. the codebase | Rejected |
| Body-link inline type | Reopens KOM:222-vs-roadmap:64 (user already decided frontmatter) | Rejected |

`target` is a **bundle-relative concept-id, `.md` stripped** — byte-identical to how
`provenance` (`sources/<slug>`) and `MergeLedgerEntry.absorbed_id` reference objects
today. Not a `/…​.md` link, not a bare slug.

**Guards** (fail-closed, mirroring `build_concept` single-line + `_canonicalize_concept_id`):
`target`/`type` non-empty after strip, no `\n`/`\r` (blocks index/log/YAML newline
injection), `target` passes canonicalization (no absolute, no `..`, no reserved
basename). **Field order**: within an entry emit `target` then `type`; the `relations`
key is inserted after `provenance`, before any `merged_from`; entries **sorted by
`(target, type)` on every write** so re-emission and dedup are deterministic. New
`okf.encode_relations`/`decode_relations` round-trip it, `ValueError` on any malformed
shape. `build_concept` does **not** emit `relations` — the LLM ingest path stays
byte-identical.

### Decision: Relation vocabulary — seeded-but-open

New leaf module `src/openkos/model/relations.py` (zero `openkos` imports, mirrors
`types.py`): `SEEDED_RELATION_TYPES` = KOM's 8 (`references, depends_on,
derived_from, related_to, caused_by, part_of, member_of, produced_by`). `relate`
WARNs to stderr on a type not in the set but writes it anyway (KOM:336 "recommendation,
never a constraint"). Empty/whitespace type is **rejected** at the encode layer
(fail-closed). Widening later is backward-compatible.

### Decision: Merge guard — REFUSE (fail-closed), no rewiring

**Chosen: REFUSE**, not warn-and-proceed. Rationale: the project is fail-closed and
reversibility-first (ADR-0002); proceeding orphans typed edges with no rewiring until
slice 2 — silent graph corruption that violates the "never silent" ethos. Refusal is
trivially reversible (nothing is written). Escape hatch: slice 2 rewiring, or manual
edit. Full frontmatter-edge rewiring + ledger extension stays OUT (slice 2).

## Data Flow

    relate a references b ─┐
      Phase A: resolve a,b (both MUST exist) ─ WARN if unknown type
      encode_relations → a.md frontmatter ── preview ── confirm/--auto/review:
                                                             │
    check_conformance ── §9 additive rule validates relations shape
    build_graph ── frontmatter relations ──→ Edge(relation_type=type)
    merge x y ── find_relation_conflicts(y) ──→ inbound/outbound edge? REFUSE

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/model/relations.py` | Create | Seeded vocabulary registry (leaf) |
| `src/openkos/model/okf.py` | Modify | `encode_relations`/`decode_relations`; §9 additive rule in `check_conformance` |
| `src/openkos/cli/main.py` | Modify | New `relate` verb (forget-shaped); `**Relate**` log line |
| `src/openkos/graph/sqlite_graph.py` | Modify | Populate `Edge.relation_type` from frontmatter relations |
| `src/openkos/bundle/merge.py` | Modify | Pure `find_relation_conflicts` scanner |

## Interfaces / Contracts

```python
# model/okf.py
def encode_relations(rels: list[Relation]) -> list[dict[str, str]]: ...
def decode_relations(metadata: dict[str, object]) -> list[Relation]:
    """[] if key absent; ValueError on non-list / bad entry / empty|multiline field."""

# model/relations.py
SEEDED_RELATION_TYPES: frozenset[str]   # KOM's 8

# bundle/merge.py — detection only, no rewiring
def find_relation_conflicts(absorbed_id: str, files: Mapping[str, str],
                            absorbed_text: str) -> list[str]:
    """Human-readable conflict lines: absorbed's OWN relations (outbound) +
    any other/survivor file whose relations target == absorbed_id (inbound)."""
```

**`relate` contract**: `openkos relate <source> <rel> <target> [--auto]`. Phase A —
`require_workspace`; `_resolve_concept_path` on **both** source and target (fail-closed
existence + path containment); reject `source == target`; WARN on unknown `rel`; dedup
`(target, rel)` (idempotent no-op if present). Writes only `source`'s concept file +
`log.md` (no `index.md` — relations have no catalog bullet). Reuses the preview →
confirm/`--auto`/`review:` gate verbatim. Phase B: write source concept, then `log.md`.

**§9 rule**: in the `check_conformance` rules-1/2 loop, when `scan.metadata` has a
`relations` key, call `decode_relations`; on `ValueError` append `f"{scan.path}: {msg}"`.
Runs only when the key exists ⇒ rules-1/2 output byte-identical otherwise
(regression-guarded like `test_check_conformance_round_trip_regression`).

**Graph projection**: extend the node pass to also carry each doc's decoded
`relations`; after the untyped `_LINK_RE` pass (line 237 unchanged — still inserts
`None`), add typed edges `(source_id, target, type)` for targets in `node_ids`
(drop-if-unknown, consistent with today). Dedup key becomes
`(source_id, target_id, relation_type)`; a typed edge and an untyped body-link between
the same pair coexist as two rows. Extend `_SELECT_EDGES_SQL` `ORDER BY` to include
`relation_type` (NULLs first) for deterministic output.

## Testing Strategy

| Layer | What | Approach |
|-------|------|----------|
| Unit | encode/decode round-trip, malformed shapes, sort/dedup | pytest, fail-closed cases |
| Unit | §9 rule fires only on `relations`; regression byte-parity | golden compare |
| Unit | `find_relation_conflicts` inbound+outbound | in-memory files map |
| Unit | `build_graph` typed edges, untyped path byte-unchanged | assert edges |
| Integration | `relate` fail-closed on missing target; WARN unknown; confirm/--auto; guard REFUSE | Typer runner |

## Threat Matrix

| Row | Status | Safe behavior / RED test |
|-----|--------|--------------------------|
| Path traversal via `source`/`target` concept-id | Applicable | `_resolve_concept_path`/`_canonicalize_concept_id` reject `..`, absolute, reserved; RED test with `../../evil` |
| Newline injection via `rel`/`target` into log/frontmatter | Applicable | Encode-layer single-line guard; RED test with embedded `\n` |
| Shell/subprocess/routing/VCS automation | N/A | No process integration; pure file writes |

## PR-Slice Plan (auto-chain, feature-branch-chain)

Tracker: `feat/typed-relationships` (only tracker merges to main, human checkpoint).
Each slice < 200 authored lines incl. tests — under the 400 budget.

| PR | Scope | ~Lines | Targets |
|----|-------|--------|---------|
| 1 | `model/relations.py` + okf encode/decode + §9 rule + regression | ~180 | tracker |
| 2 | `relate` CLI verb + `**Relate**` log + tests | ~150 | PR1 |
| 3 | `build_graph` typed-edge population + ordering/dedup + tests | ~90 | PR2 |
| 4 | `find_relation_conflicts` + merge REFUSE hook + tests | ~110 | PR3 |

## Migration / Rollout

No migration — `relations:` is a new optional key; existing bundles stay conformant,
`build_graph` reverts to `NULL` relation_type on revert. New **ADR-0004** required:
"Typed relationships in frontmatter; guard-then-rewire staging" — records frontmatter
storage (KOM:222 over roadmap:64), seeded-open vocabulary, and merge-REFUSE-in-slice-1
with reversible rewiring deferred to slice 2 (extends ADR-0002 ledger later).

## Open Questions

- [ ] None blocking. Slice-2 rewiring design (ADR-0002 ledger extension) is out of scope here.
