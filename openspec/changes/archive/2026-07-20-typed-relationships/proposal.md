# Proposal: Typed Relationships (Slice 1 — deterministic edges)

## Intent

Today the bundle is a bag of nodes: objects carry only prose backlinks to their Sources, and the shipped `graph/` projection has zero consumers with `Edge.relation_type` always `None`. MVP-2 turns it into a real typed graph. This is **slice 1 of N**: ship the storage format, a deterministic write path, and the first graph consumer — deferring LLM edge discovery and full merge rewiring to later slices so the hard plumbing is de-risked without model uncertainty (same staging the entity-resolution chain used).

## Scope

### In Scope
- **OKF `relations:` frontmatter key** — first-class list of `{target, type}` per object (KOM §222; resolves the KOM-vs-roadmap:64 conflict in favor of KOM/frontmatter).
- **`openkos relate <source> <rel> <target>` CLI verb** — fail-closed (target object MUST exist), writes into source frontmatter, catalogs/logs, review-gated like other write verbs (Phase A compute-no-write → preview → confirm/`--auto`, honors `review:` config). No LLM.
- **§9 conformance rule** for the `relations:` field shape.
- **Seeded-but-extensible vocabulary** — KOM's 8 candidate types as known defaults; any non-empty string allowed; WARN (never reject) on unknown (KOM §336).
- **Graph projection** — `build_graph` POPULATES `Edge.relation_type` from `relations:` (the minimum visible consumer).
- **Merge guard** — merge DETECTS and is NOT-silent (warn or refuse) when absorbing an object bearing inbound/outbound typed relations.

### Out of Scope (deferred to later slices)
- LLM propose-then-adjudicate edge production.
- Full reversible frontmatter-edge rewiring through merge/unmerge (ADR-0002 ledger extension).
- User-facing relations query/graph read CLI surface.
- Inverse/symmetric-relation bookkeeping; embeddings/hybrid retrieval; untyped-edge behavior changes.

## Capabilities

### New Capabilities
- `typed-relationships`: the `relate` verb contract, `relations:` frontmatter format, and the seeded-extensible vocabulary.

### Modified Capabilities
- `ingestion`: add §9 conformance rule for the `relations:` frontmatter field.
- `graph-projection`: `relation_type` is populated from frontmatter (no longer always null).
- `entity-resolution-merge`: add non-silent guard for edge-bearing objects.

## Approach

New frontmatter key parsed by `okf`; `relate` reuses the existing Phase-A/preview/confirm write scaffold and validates target existence deterministically. Vocabulary is a single registry (mirrors `types.py::REGISTRY`) used for WARN-only classification. `sqlite_graph.build_graph` reads `relations:` to set `relation_type`; the existing untyped `_LINK_RE` path is untouched. Merge gains a detection-only guard (no rewiring).

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/model/okf.py` | Modified | `relations:` field in build/parse; §9 rule |
| `src/openkos/cli/main.py` | Modified | new `relate` verb, catalog/log |
| relation vocabulary registry | New | seeded-extensible set, WARN-on-unknown |
| `src/openkos/graph/sqlite_graph.py` | Modified | populate `Edge.relation_type` |
| `src/openkos/bundle/merge.py` | Modified | non-silent edge-bearing guard |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Deferred merge rewiring silently orphans edges | High | Slice-1 non-silent guard (warn/refuse); full fix = slice 2 |
| Rigid ontology (KOM §336) | Med | Open vocabulary, WARN not reject; widening stays backward-compatible |
| Dangling/forward-reference edges | Med | `relate` fails closed on missing target |
| >400-line review budget (OKF+§9+CLI+graph+merge) | High | `delivery_strategy=auto-chain`, `chain_strategy=feature-branch-chain`; expect stacked PRs |

## Rollback Plan

Deterministic and additive. Revert the slice's commits/PRs; `relations:` is a new optional key, so existing bundles stay conformant and `build_graph` reverts to null `relation_type`. No migration; no data loss.

## Dependencies

None new. Reuses existing Phase-A write scaffold, `review:` config, and `graph/` package.

## Success Criteria
- [ ] `openkos relate a references b` writes a typed relation into `a`'s frontmatter after confirm; `--auto`/`review:` honored.
- [ ] `relate` to a non-existent target fails closed with a clear error, no write.
- [ ] Unknown relation type is accepted with a WARN, not rejected.
- [ ] Bundle with `relations:` passes `check_conformance`; malformed shape is reported.
- [ ] `build_graph` emits edges whose `relation_type` matches frontmatter.
- [ ] Merging an edge-bearing object surfaces a non-silent guard (warn or refuse), never silent.
- [ ] `uv run pytest` green; 90% branch coverage held.
