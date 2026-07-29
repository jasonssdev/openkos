# Proposal: Re-ingest Must Not Lower a Source's Sensitivity

## Intent

Issue #229. Re-ingest is a silent declassification path. `main.py:1667-1676` builds the
Source concept from `cfg.default_sensitivity` and never reads disk; `:1794`
`write_atomic(concept_path, ...)` then overwrites the on-disk Source — so a level a human
raised with `set-sensitivity` is reset, with no `--allow-downgrade` and no prompt, routing
around the exact gate ADR-0008 exists to enforce. `:1684` reads the stamp back from that
same in-memory string, so newly extracted derived objects inherit the wrong value too
(`:1804` is create-only, so existing derived objects are untouched). One root cause, two
symptoms.

## Decisions

| Decision | Call | Rationale |
|---|---|---|
| Which fix | **(b) high-water-mark**: `okf.combine_sensitivity(on_disk, cfg.default_sensitivity)` | (b) dominates (a) "read-and-reuse". Neither can lower a Source, so (a) buys no downgrade escape hatch — but (a) also ignores a *raised* workspace default, letting a Source sit below the `workspace_floor` that gates its own LLM send. (b) is fail-closed by construction, reuses ADR-0003's primitive, and its `_rank` already fails closed on dirty frontmatter. |
| Downgrade path | Only `set-sensitivity --allow-downgrade` (ADR-0008). A feature, not a trap: re-ingest is a bulk mechanical verb, not a deliberate reclassification. |
| Scope | **Both halves.** Fixing only the stamp leaves `:1794` still downgrading the document. |
| Refresh semantics | Merge one field into the newly built metadata, never restore the old document. `timestamp`, `description`, `resource`, `provenance`, body all refresh as today. |
| Visibility | **Reported.** Re-ingest preview (`:1763`) names the preserved level; the archived cycle set the precedent that a sensitivity write is never silent. |
| ADR | New ADR-0010. ADR-0003/0008/0009 stay unedited (immutable once Accepted). |

## Invariant that must not break

`_stage_derived_objects(workspace_floor, stamp_sensitivity)` keeps two separate
parameters. `workspace_floor` MUST stay literally `cfg.default_sensitivity` (the
`extract` LLM gate, `sensitivity-aware-llm` Req 4). Only `stamp_sensitivity` and the
bytes written to `concept_path` change. Pinned by
`tests/unit/cli/test_ingest.py:1899-1938`.

## Scope

### In Scope
- Re-ingest resolves the Source's sensitivity as the high-water mark of the on-disk value
  and the config default; that value is written to `concept_path` and passed as
  `stamp_sensitivity`.
- Preview reports a preserved level.
- Tests combining `set-sensitivity` + `regenerate=True` + a newly staged derived object
  (currently zero coverage — see exploration Finding 6).
- ADR-0010; `ingestion` delta spec.

### Out of Scope
- Bulk backfill of existing bundles (#231).
- Merge-orphaned provenance (#230).
- Existing derived objects' sensitivity — create-only stays create-only.
- Fresh (non-regenerate) ingest, `set-sensitivity`, `sensitivity-aware-llm`,
  `sensitivity-config`: unchanged.
- Multi-source high-water-mark (MVP-2/3).

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `ingestion`: "Default Sensitivity from Config" (`spec.md:658-668`) gains a re-ingest
  clause — the config default is a floor on regenerate, not an assignment; plus a preview
  reporting scenario.

## Approach

1. Under `regenerate`, read existing `concept_path` frontmatter when present.
2. `resolved = combine_sensitivity(on_disk, cfg.default_sensitivity)`; absent file falls
   through to the config default.
3. Build the Source with `sensitivity=resolved`; pass it as `stamp_sensitivity`; leave
   `workspace_floor` untouched.
4. Preview names the preserved level when it exceeds the config default.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/cli/main.py:1666-1695` | Modified | Resolve + build + stamp |
| `src/openkos/cli/main.py:1757-1767` | Modified | Preview reporting |
| `openspec/specs/ingestion/spec.md` | Modified | Delta spec |
| `docs/adr/0010-*.md` | New | Re-ingest is raise-only |
| `tests/unit/cli/test_ingest.py` | Modified | Coverage gap |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| A merged/on-disk value leaks into `workspace_floor` | Med | Existing pinning test; assert explicitly in the delta spec |
| Dirty on-disk `sensitivity` ranks `confidential` and silently escalates | Low | ADR-0003 intent; preview reports the resolved level |
| A user lowering `default_sensitivity` expects re-ingest to apply it | Low | Documented; `set-sensitivity --allow-downgrade` is the verb |
| >400-line PR | Low | Single narrow call site |

## Rollback Plan

Revert the change branch. The fix only ever raises, so no data is lost; an
over-classified Source can be lowered deliberately via `set-sensitivity --allow-downgrade`.

## Dependencies

- ADR-0003 (`combine_sensitivity`), ADR-0008 (downgrade gate), ADR-0009 (propagation).

## Success Criteria

- [ ] Re-ingest of a Source raised to `confidential` leaves it `confidential`.
- [ ] Derived objects newly created on that re-ingest are stamped `confidential`.
- [ ] Raising `default_sensitivity` above the on-disk value still raises on re-ingest.
- [ ] `timestamp` and other refreshed fields still refresh.
- [ ] `test_extract_gate_still_reads_workspace_floor` still passes unmodified.
- [ ] Existing derived objects remain byte-untouched.

## Proposal question round

Ran in `auto` mode; no interactive round was possible. Assumptions needing user review:
1. High-water-mark (b) over read-and-reuse (a) — re-ingest can raise but never lower.
2. Both halves fixed in one change (Source document + derived stamp).
3. The preserved level is reported in the preview, not silent.
4. No new flag on `ingest`; `set-sensitivity --allow-downgrade` stays the only downgrade.
