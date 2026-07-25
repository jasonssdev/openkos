# Proposal: adjudicate --apply-same (guarded batch merge)

## Intent

Close issue #137 by shipping its final part: a guarded batch verb that fuses
every eligible SAME 2-member group in one operation. The shipped `--apply`
walk confirms one pair at a time, which is slow for large duplicate sets. The
issue demands a batch that "must not blindly merge" — so the guard is a typed
count over a full preview, not a bare `[y/N]`. #147 (deferred dependency #138)
improved verdict quality but added NO machine-checkable field: confidence is
still uncalibrated, so `verdict == SAME` + 2-member group size remains the only
trustworthy programmatic gate. The preview + typed-count + reversibility are the
safety net.

## Scope

### In Scope
- New `--apply-same` flag on the existing `adjudicate` command; mutually
  exclusive with `--apply` and `--json` (mirror the `--apply`/`--json` exit-2
  pattern, checked before the workspace gate).
- New `_run_adjudicate_apply_same` path: same adjudication (`find_candidates` →
  `adjudicate_candidates`), filter to `verdict==SAME AND len(member_ids)==2`,
  build and print ONE aggregate preview (survivor <- absorbed per line),
  typed-count confirmation gate, then sequential merge reusing the shipped
  per-pair body (`_resolve_concept_path` re-verify, `prepare_merge`,
  `merge_core`, `_autocommit` + ledger).
- Reversibility documented: each merge lands a `merged_from` entry; a bad batch
  is recovered via N sequential LIFO `unmerge` calls.

### Out of Scope (Non-Goals)
- `--min-confidence` flag — confidence is uncalibrated post-#147; would give
  false safety.
- Batch-size cap.
- New batch-undo command — reversibility is N sequential `unmerge`.
- Applying N>2 groups — SKIPPED, identical to `--apply`.

## Capabilities

### New Capabilities
None

### Modified Capabilities
- `entity-resolution-adjudication`: add `--apply-same` batch verb requirements
  (aggregate preview, typed-count confirmation, eligibility filter reuse, mutual
  exclusion with `--apply`/`--json`, mid-run stop-keep-prior semantics, unmerge
  round-trip).

## Approach

Two-pass in `_run_adjudicate_apply_same`. Pass 1: filter eligible SAME 2-member
groups, print one aggregate preview block (reusing the per-pair preview format
`_run_adjudicate_apply` already emits: `merge {absorbed} into {survivor}
(sensitivity X->Y, N rewrite(s), removes bundle/{absorbed}.md)`). Then prompt
the operator to TYPE THE EXACT NUMBER of listed merges. Empty Enter or any
wrong/mismatched value ABORTS with zero writes. Pass 2 (only on exact match):
loop the eligible pairs, re-verify ids per pair (skip already-merged), then
`prepare_merge`/`merge_core`/`_autocommit` + ledger commit per merge — the same
functions `_run_adjudicate_apply` uses (main.py:516-631). A mid-run
`(OSError, ValueError)` stops the run but keeps prior per-merge commits, same as
`--apply`. Final summary line prints applied/skipped counts.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/cli/main.py` | Modified | New `--apply-same` flag + mutual-exclusion checks in `adjudicate()`; new `_run_adjudicate_apply_same` helper |
| `tests/unit/cli/test_adjudicate.py` | Modified | New batch tests mirroring the `--apply` fixture/monkeypatch matrix |
| `openspec/specs/entity-resolution-adjudication/spec.md` | Modified | Delta spec for `--apply-same` |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Batch is destructive; one blind mistake compounds N times | Med | Full aggregate preview + typed-count gate; every merge reversible via `unmerge` |
| Typed-count gate accepts a mismatched/empty value and writes | Low | Gate MUST be exact-match; empty or any mismatch ABORTS with zero writes — pinned by tests |
| Confidence assumed as a guard | Low | Explicit non-goal; uncalibrated post-#147 |
| Chained survivors during batch invalidate later ids | Med | Re-verify both ids per pair before merge; skip already-merged (same as `--apply`) |

## Rollback Plan

No merge writes until the typed count exactly matches, so an aborted gate leaves
the workspace untouched. After a completed batch, reverse via N sequential LIFO
`unmerge <survivor> <absorbed>` calls (newest first per survivor chain); each
merge has an independent `merged_from` ledger entry and its own commit. Revert
the code change itself by dropping the `--apply-same` branch — the shipped
`--apply` path is unaffected.

## Dependencies

- #147 (verdict-quality prompt fix) — resolved; prompt-only, no new field.
- merge-core-extraction (#163) — shipped; `prepare_merge`/`merge_core` reused.

## Success Criteria

- [ ] `adjudicate --apply-same` prints one aggregate preview of every eligible
      SAME 2-member merge before any write.
- [ ] Typed count matching the listed total proceeds; empty/wrong/mismatched
      value aborts with zero writes.
- [ ] N>2 groups and non-SAME verdicts are excluded from the batch.
- [ ] `--apply-same` is rejected (exit 2) when combined with `--apply` or `--json`.
- [ ] Mid-run failure stops remaining merges but keeps prior commits.
- [ ] Applied merges round-trip via sequential `unmerge`.
- [ ] Issue #137 is CLOSED by this slice.
