# Proposal: Entity-Resolution Candidate Generation (read-only)

> Slice 1 of a 2-3 change mini-chain (MVP-2 boundary problem). This slice is READ-ONLY candidate generation only. Adjudication (slice 2) and reversible merge (slice 3) are separate later changes.

## Intent

Today identity is a title-derived slug (`cli/main.py:146-156`) and the only cross-object check is `derived_path.exists()` (`cli/main.py:297-306`). A second source that words the same real entity differently (e.g. "Stoicism" vs "Stoic Philosophy") slugifies differently and silently fragments into unlinked objects; an identical slug is silently dropped. `docs/cli.md:82` already names this as the MVP-2 gap. This slice makes fragmentation VISIBLE without touching anything: a read-only pass that proposes candidate pairs/groups that MIGHT be the same entity, with a why/confidence, for human review. It never decides, never writes.

## Scope

### In Scope
- A whole-bundle read-only pass producing CANDIDATE pairs/groups via deterministic title-normalization blocking (case/whitespace/punctuation/diacritic folding), type-scoped (Concept only matches Concept).
- Each candidate carries the two concept_ids, a normalized-key match reason, and a confidence signal — a proposal, not a decision.
- A pure library function as the load-bearing surface; results as an ephemeral returned structure (dataclasses).
- A thin read-only CLI report verb rendering candidates, following the `lint`/`status` shape (no writes, no confirmation, no `--auto`).

### Out of Scope (explicit non-goals)
- LLM adjudication of candidates (slice 2).
- Destructive `merge`/`resolve` verb, merge record, tombstone, sensitivity recompute, un-merge (slice 3).
- Embedding/vector-based candidate generation (separate later MVP-2 deliverable).
- Any write/mutation of the bundle; any change to `ingest`'s single-source Phase A/B contract.
- Stable/content-based concept ids (separate concern).

## Capabilities

### New Capabilities
- `entity-resolution-candidates`: read-only cross-source candidate generation over the bundle (title-normalization blocking, type-scoped, ephemeral report).

### Modified Capabilities
- None. `ingest` and its single-source contract are untouched.

## Approach

- **Where it lives**: a NEW `src/openkos/resolution/` package. Extraction (`extraction/concept.py`) is a config-free, single-source LLM leaf; resolution is a distinct cross-source, derived analysis over the whole bundle. A separate package keeps the screaming-architecture layering clean and never mutates canonical.
- **Primary signal**: deterministic title-normalization blocking, type-scoped (existing type-folder segregation blocks most cross-type false positives for free).
- **Secondary (optional, not required)**: graph-neighborhood/provenance overlap as a confidence booster only. Embedding similarity explicitly deferred.
- **Representation**: candidates stay EPHEMERAL — a returned structure plus a rendered report. NOT a new persisted OKF doc type, frontmatter marker, or `bundle/` state file. This avoids hardening a candidate record into a 10th pseudo-type (principle 4: no rigid ontology) and keeps everything reconstructible from canonical + git.
- **Surface**: a pure `resolution.find_candidates(...)` function is the contract; a read-only CLI report verb (distinct from `lint`, name finalized in spec) renders it. Being read-only, it needs no confirm gate.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/resolution/` | New | Package: normalization, blocking, candidate dataclasses, report. |
| `src/openkos/cli/main.py` | Modified | Register one read-only report verb; no change to `ingest`/`forget`. |
| `docs/cli.md` | Modified | Document the new read-only verb alongside `lint`/`status`. |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Candidate record hardens into a pseudo-type (principle 4) | Med | Keep ephemeral: no persisted type, no `bundle/` state. |
| Normalization too crude — misses paraphrases / coincidental same-title-different-thing | High | Frame output as candidates for review, not decisions; adjudication (slice 2) filters; type-scoping curbs false positives. |
| Scope creep into merge/adjudication | Med | Hard non-goals above; slice boundary enforced. |

## Rollback Plan

Additive and read-only. Remove the `resolution/` package and unregister the CLI verb; no bundle state is created, so nothing to migrate or clean up. Plain git revert of the PR fully reverses it.

## Dependencies

- None new. Uses only the existing bundle scan and concept_id model. No LLM, no embeddings.

## Success Criteria

- [ ] A library function returns candidate pairs/groups from a whole-bundle scan, type-scoped, with match reason + confidence.
- [ ] "Stoicism" vs "Stoic Philosophy"-style fragmentation surfaces as a candidate; identical-slug collisions surface too.
- [ ] A read-only CLI verb renders candidates with zero writes, no confirmation, no `--auto`.
- [ ] No new persisted bundle type or state file; `ingest`'s contract unchanged.
- [ ] Output framed as reviewable proposals, never automatic decisions.

## Open Questions (for spec/design)

- Should blocking stay strictly per-type or deliberately cross type boundaries (KOM's Stoicism example straddles this)?
- Exact confidence signal shape (normalized-key exact vs. token-overlap/edit-distance tiers)?
- Final CLI verb name and its exact stdout/stderr report format (align with `lint`/`status`).
