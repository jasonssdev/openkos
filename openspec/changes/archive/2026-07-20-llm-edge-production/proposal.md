# Proposal: LLM Edge Production (MVP-2 Slice 2b — LLM-suggested types for untyped links)

## Intent

Slice 1 shipped typed relations (`relations:` + deterministic `relate`); slice 2a made `merge` edge-aware. But the graph still projects many UNTYPED edges — bundle-relative body-markdown links (`[text](/path.md)`, stored with `relation_type = NULL`) that carry no semantic type. Typing them by hand is tedious and inconsistent. Slice 2b adds an LLM step that reads existing untyped body-link edges and SUGGESTS a relation type for each; the human confirms and writes via the existing `relate` verb. Human-in-the-loop, zero new write path. This is slice 2b of slice 2 (2a = `merge-edge-rewiring`, done).

## Scope

### In Scope
- New READ-ONLY CLI verb (proposed `suggest-relations`, mirrors `adjudicate`): lists existing untyped body-link edges (source→target, NULL relation_type) each with an LLM-suggested `type` + rationale; instructs the human to confirm via `openkos relate`.
- New leaf LLM module (mirrors `resolution/adjudication.py`): 2-message prompt → raw string → fail-closed JSON extraction → per-item validation, behind `LLMBackend`/`OllamaClient`. No retry, no Pydantic.
- Candidate set = existing untyped body-link edges ONLY, read from the derived graph projection (read-only). Suggested type checked via existing `validate_relation_type`.

### Out of Scope (deferred)
- Batch-write verb that writes edges directly (Approach 2).
- `Relation` provenance/confidence fields (Approach 3) — LLM-suggested and human edges stay byte-identical once written.
- Discovering NEW edges between unlinked objects (whole-bundle pairwise).
- Migrating the 3 existing LLM consumers to Pydantic/retry.
- Relation-vocabulary or graph-projection schema changes.

## Capabilities

### New Capabilities
- `llm-edge-production`: read-only LLM verb that suggests relation types for existing untyped body-link edges; human confirms via `relate`.

### Modified Capabilities
None.

## Approach

Mirror the `adjudicate`→`merge` split, one layer over. A candidate generator reads untyped edges (NULL relation_type) from the derived graph projection (read-only); a new `resolution`-style LLM module proposes a type per edge; a new read-only CLI verb prints `(source, suggested type, target, rationale)` and tells the human to run `relate`. Writes happen ONLY through the existing deterministic `relate` path — its fail-closed validation, containment, idempotency, and confirm gate are reused end-to-end. Layering held: LLM behind `LLMBackend`; the verb reads derived `graph`, but canonical `model`/`bundle`/`state` are untouched and never import `graph`. Slice-2a reversibility is untouched — no writes means no ledger/unmerge surface.

## ADR Assessment

No new ADR warranted: reuses established patterns (adjudicate/relate split, hand-rolled LLM parsing), zero schema and zero interface changes, read-only and trivially reversible. Fails the "hard-to-reverse" gate.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/resolution/edges.py` (or similar) | New | LLM module: prompt → fail-closed JSON → per-item type validation |
| `src/openkos/cli/main.py` | Modified | New read-only `suggest-relations` verb; candidate read from graph |
| `src/openkos/graph/*` | Read-only | Source untyped body-link edges (NULL relation_type) |
| `tests/unit/...` | New | Verb + LLM-module unit tests, fail-closed parse cases |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Verb name/UX drifts from `adjudicate` precedent | Low | Follow `adjudicate` wiring shape exactly |
| LLM emits invalid/unknown type | Med | `validate_relation_type` per item; bad items degrade, never crash |
| Scope creep into producer framework | Med | Keep as CLI-wired leaf module, no new abstraction |
| Untyped-edge candidate volume large on big bundles | Low | Read-only, no writes; batching deferred to Approach 2 |

## Rollback Plan

Revert the change/PR. The verb is read-only and additive — no writes, no schema, no migration, nothing to restore. Removing it leaves slice-1/2a behavior byte-identical.

## Dependencies

Builds on slice 1 (`relate`, `relations:`, vocabulary, `validate_relation_type`) and the graph projection's untyped-edge read. `LLMBackend`/`OllamaClient` already present. No new runtime deps.

## Success Criteria

- [ ] Verb lists every untyped body-link edge with an LLM-suggested valid type + rationale.
- [ ] Verb performs ZERO writes; human writes accepted edges via `relate`.
- [ ] LLM parse is fail-closed: malformed items degrade, never crash the verb.
- [ ] Layering held: no canonical (`model`/`bundle`/`state`) import of `graph`.
- [ ] `uv run pytest` green; 90% branch coverage held.
