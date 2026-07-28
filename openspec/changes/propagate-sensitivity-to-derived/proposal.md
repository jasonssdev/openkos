# Proposal: Propagate Source Sensitivity to Derived Objects

## Intent

Issue #219. No source→derived sensitivity edge exists: `ingest` stamps the Source
(`cli/main.py:1660`) and each derived object (`:1674`) with the same config constant —
siblings, not parent→child. So raising a Source's sensitivity leaves its derived objects
readable by every `llm.chat` gate, which all read the document's own stored field.
`openspec/specs/ingestion/spec.md:414-427` already claims verbatim inheritance, so this is
a **spec correction plus a privacy fix**, not a new feature.

## Decisions

| Decision | Call | Rationale |
|---|---|---|
| Which edge | Derived object's `provenance: ["sources/<slug>"]` frontmatter | Only durable, resolvable parent pointer. Survives re-ingest (create-only leaves files byte-untouched). |
| Edge survival gap | Merge of an absorbed Source orphans `provenance` (`links.py`/`relations.py` rewrite bodies and `relations:`, never `provenance:`) | **Out of scope** — a merge link-integrity defect, independent of sensitivity, needing its own spec/ADR change. Follow-up issue. Here we only **fail closed**: unresolvable provenance is warned, never treated as "no source", never lowers a value. |
| Where propagation runs | **Creation time (made real) + set time (write-through)** | Read-time rejected: rewrites `sensitivity-aware-llm` Req 1 ("resolve from its own field"), touches 8 requirements / 6 call sites, adds a provenance walk to every gate, and leaves the on-disk file still mislabeled for git/grep/humans. Creation-time alone rejected: that is today's coincidence. Set time is the human's only deliberate correction verb — rare, already preview-gated and auto-committed. |
| Direction | Raise-only, reusing `okf.combine_sensitivity` (ADR-0003) — never reimplemented | A Source downgrade must not silently declassify derived objects; consistent with ADR-0008's gated-downgrade posture. |
| Visibility | Reported, not silent: the existing preview lists every derived object to be raised; success message and auto-commit include them; `--auto` skips the prompt as elsewhere | Propagation is a multi-file write; a human must see it. |
| MVP | Single-source staleness is **MVP-1, in scope**. Multi-source high-water-mark stays deferred per ingestion non-goal `:23` ("sensitivity high-water-mark across multiple sources (MVP-2/3)") | That non-goal defers combining *several* sources. Derived objects have single-entry provenance today (`cli/main.py:1356`), so the deferred case cannot yet arise. |
| Reverse lookup | Reuse `bundle/provenance.py::find_provenance_descendants` | Existing whole-bundle closure; no new index, no reliance on the body-link-derived `sqlite_graph` `derived_from` projection. |

## Scope

### In Scope
- Ingest: derived objects inherit the built Source's own resolved `sensitivity`, not the config constant.
- `set-sensitivity` on a Source-typed concept: raise its provenance descendants via `combine_sensitivity`.
- Preview/success/`--help` text updated to state the new bounded scope honestly.
- New ADR superseding ADR-0008's "no sibling and no derived object" scope statement.
- Tests that distinguish real propagation from coincidence (fix `test_ingest.py:1824`) and invert `test_set_sensitivity.py:355-381`.

### Out of Scope
- Repairing merge-orphaned `provenance` (follow-up issue).
- Bulk backfill / `reconcile --sensitivity` over existing bundles.
- Multi-source high-water-mark (MVP-2/3).
- Read-time computed sensitivity; `sensitivity-aware-llm` is unchanged.
- Lowering derived sensitivity by any path.
- `unmerge` behaviour for third-party derived objects.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `sensitivity-config`: "Scope Is Exactly One Named Concept" (`spec.md:205-219`) narrows to "the named concept, plus raise-only propagation to its provenance descendants when it is a Source"; preview and honesty-line requirements follow.
- `ingestion`: "Derived Object Provenance and Sensitivity Inheritance" (`:414-427`) restated so the claimed verbatim inheritance is backed by a real read of the Source's value.

## Approach

1. Ingest passes the built Source's `sensitivity` into `_stage_derived_objects` instead of `cfg.default_sensitivity`.
2. `set_sensitivity_cmd` gains a Source-typed branch: resolve descendants via `find_provenance_descendants`, compute `combine_sensitivity(existing, new)` per object, stage only strict raises, show them in the preview, write in the same transaction/commit.
3. Unresolvable provenance entries emit a warning and are excluded — fail closed, never lower.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/cli/main.py:1653-1679`, `:1178-1367` | Modified | Real creation-time inheritance |
| `src/openkos/cli/main.py:3044-3230` | Modified | Set-time write-through, preview, messages |
| `src/openkos/bundle/provenance.py` | Modified | Reverse lookup reused/exposed |
| `openspec/specs/{sensitivity-config,ingestion}/spec.md` | Modified | Delta specs |
| `docs/adr/0009-*.md` | New | Supersedes ADR-0008 scope statement |
| `tests/unit/cli/test_{ingest,set_sensitivity}.py` | Modified | Distinguishing + inverted assertions |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| ADR-0008 conflict | High | New superseding ADR; ADR-0008 left immutable |
| Multi-file write surprises a user | Med | Preview lists every raise before write |
| Large bundle scan on `set-sensitivity` | Low | Rare human verb; single closure pass |
| Orphaned provenance hides a descendant | Med | Warn loudly; tracked as follow-up |
| >400-line PR | Med | Slice: (1) ingest inheritance + spec, (2) set-time propagation + ADR |

## Rollback Plan

Revert the change branch. Propagation only ever raised values, so no data is lost;
already-raised derived objects keep their higher (fail-closed) sensitivity and can be
lowered deliberately via `set-sensitivity`'s gated downgrade path.

## Dependencies

- ADR-0003 (`combine_sensitivity` monotonic rule), ADR-0008 (superseded in scope only).

## Success Criteria

- [ ] Raising a Source's sensitivity raises every derived object in the same run.
- [ ] Lowering a Source never lowers a derived object.
- [ ] A test fails if creation-time inheritance is replaced by a shared constant.
- [ ] `sensitivity-aware-llm` requirements and call sites are unchanged.
- [ ] Ingestion spec's inheritance claim is backed by code.

## Proposal question round

Run in `auto` mode; no interactive round was possible. Assumptions needing user review:
1. Merge-orphaned provenance is deferred to a follow-up issue rather than fixed here.
2. Propagation is raise-only; a Source downgrade deliberately does not cascade.
3. No bulk backfill verb — existing bundles are protected at the next `set-sensitivity`.
4. Propagation follows the full provenance descendant closure (depth-1 in practice today).
