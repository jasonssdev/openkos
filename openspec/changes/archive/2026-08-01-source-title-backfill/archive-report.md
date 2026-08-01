# Archive Report: source-title-backfill

**Change**: `source-title-backfill` · **Issue**: [#298](https://github.com/jasonssdev/openkos/issues/298) · **Closed**: 2026-08-01 · **Status**: COMPLETE

## Delivery Summary

The `source-title-backfill` change is now closed and archived. All 37 implementation tasks are complete. Verification passed with clean gates: 2931 tests green, ruff and mypy clean across 160 files. Three PRs merged to main:

- PR #302 (commit `20907ac`): Slice 1 — pure core (`bundle/source_titles.py`, `titleize`, `scan_source_titles`, `resolve_source_title_backfill`, `retitle_document`)
- PR #303 (commit `72d153f`): Slice 2 — `relabel_index_entry` primitive in `bundle/index.py`
- PR #304 (commit `7e2ad47`): Slice 3 — CLI verb `backfill-source-titles`, full Phase A/B orchestration

GitHub issue #298 closed automatically by PR #304 merge.

## What Shipped

**New capability**: `openkos backfill-source-titles` — a bundle-wide, previewed, confirmed operation that re-derives Source titles from immutable `raw/` content via `derive_source_title` (shipped in #248), updating three places per Source: frontmatter `title:`, the document's first body line `# {title}`, and the `index.md` bullet label. One log entry and one autocommit cover the whole operation.

**Key properties**:
- Three-bucket preview: staged (re-derivable), skipped (curated by human), warned (malformed resource)
- Mechanical classification test corrects issue #298's original proposal: `title == titleize(Path(resource).stem)`, not the lossy slug variant
- Surgical patch writes exactly two byte-level edits per Source (title + first line); description and content sections untouched
- Curated titles (where on-disk title does NOT match the mechanical formula) are never rewritten
- Hand-edited first lines (where `# {old_title}` does not match exactly) are refused, not overwritten
- Idempotent: re-run on a converged bundle stages nothing and exits 0
- Confirm-gate precedence: `--auto`, `config.review == false`, TTY confirm, or non-TTY refuse

**Three settled product decisions** (held throughout delivery):
1. Historical `log.md` entries are never rewritten; only new entries appended. Readers see the original title in history, new title in current state.
2. Bundle-wide operation only; no single-concept mode. Single-Source case already served by `openkos ingest` on byte-identical file.
3. Mechanical test uses `titleize(Path(resource).stem)`, not `titleize(slug)`, to avoid the lossy lowercasing error that would have misclassified `01-Introduction.md`.

**All six non-goals held**:
- No slug, filename, or Concept ID rename
- No `.openkos/*.db` rebuild (reindex is manual-only)
- No `ingest` behavior change (titleize delegation is one-line)
- No new schema or persisted state (no ADR warranted)
- No single-concept mode argument
- Companion lint check "Source title still equals slug" is separate follow-up

## Spec Coverage

27 of 28 spec scenarios have direct covering tests. 1 scenario (block-scalar/anchor title refusal) is exercised at the unit level of its constituent `_patch_title_line` guard but not end-to-end through the CLI and resolver pipeline; this is documented as WARNING #1 in the verify-report.

All three product decisions confirmed in shipped code. All non-goals confirmed.

## Test Results (Final)

**Verdict**: PASS WITH WARNINGS

### Quality gates (verbatim)
- `uv run pytest`: 2931 passed in 87.44s ✓
- `uv run ruff check .`: All checks passed ✓
- `uv run ruff format --check .`: 160 files already formatted ✓
- `uv run mypy .`: Success, no issues in 160 source files ✓

### Coverage
- CLI main: 96% branch coverage ✓
- Whole suite: 95.67% total ✓
- Module-specific (`bundle/source_titles.py`): 94% ✓

All thresholds >= 90%.

## Open Debt (Twelve Findings)

All twelve findings from the verify-report are confirmed present in the shipped code. None is stale. Each is actionable and should be filed as a follow-up GitHub issue against #298's issue chain:

1. **Post-confirm snapshot replay**: A mid-prompt edit to a Source document is overwritten by the Phase B write, because Phase A computes all retitle content before showing the preview. The write uses pre-computed bytes, never re-reading the file system after confirm. (`src/openkos/cli/main.py:3815-3836, 3906-3924`)

2. **Final log.md write failure partial state**: If the write fails after Sources land but before `log.md` is written, the re-run cannot repair the missing log entry. The classifier keys on a Source document's title, so once written it re-classifies as `curated` and is never revisited on re-run. (`src/openkos/cli/main.py:3915-3924, src/openkos/bundle/source_titles.py:216-223`)

3. **Preview non-disclosure**: The confirm preview shows staged, skipped, and warned Sources, but does not mention that `index.md` and `log.md` will also be rewritten. Preview renders only Source documents. (`src/openkos/cli/main.py:3881-3890`)

4. **Relabel count discarded**: `relabel_index_entry` returns a tuple `(new_text, count)`, but the count is bound to `_` and never checked. A staged Source with no catalog bullet still reports success. (`src/openkos/cli/main.py:3856-3858`)

5. **Frontmatter-shape refusal label non-specific**: `resolve_source_title_backfill` catches every `ValueError` from `retitle_document` (both hand-edited first line AND block-scalar/anchor/multi-line frontmatter refusals) and files them all under `warned`/`heading-mismatch`. The `heading-mismatch` label is not honest for a frontmatter-shape refusal; a distinct reason token should be filed. (`src/openkos/bundle/source_titles.py:296-302`)

6. **YAML title wrap at 80+ chars**: A derived title longer than ~80 characters is wrapped onto two lines by `okf.dump_frontmatter`'s YAML serializer, adding a third changed line and breaking the "exactly two byte-level edits" invariant. `_TITLE_MAX_CHARS = 120` in `source_title.py`, so this occurs inside the real range. Reproduced live on titles >120 chars with spaces. (`src/openkos/bundle/source_titles.py:86-89, src/openkos/source_title.py:209`)

7. **Trailing YAML comment false negative**: The guard against trailing YAML comments in `_patch_title_line` checks for literal `" #"` (space+hash). A quoted scalar value can carry a comment with no preceding space (`title: "foo"#comment` parses to `foo`, silently dropping the comment per PyYAML), which the guard does not catch. (`src/openkos/bundle/source_titles.py:78-84`)

8. **Block-scalar refusal propagation untested**: `retitle_document` fails closed on block scalars/anchors via `_patch_title_line`, but no test drives a block-scalar fixture end-to-end through `resolve_source_title_backfill` and asserts it lands in `warned`. Only the `_patch_title_line` unit guard is tested directly. (`tests/unit/bundle/test_source_titles.py:118-131`)

9. **Malformed-catalog test narrow snapshot**: `test_a_malformed_index_refuses_before_any_write` snapshots only two files (`source_before`, `log_before`), unlike the sibling invariant test which snapshots the whole workspace. Should be widened. (`tests/unit/cli/test_backfill_source_titles.py:481-505`)

10. **Provenance untested in success path**: `test_each_retitled_document_receives_its_own_rewritten_bytes` asserts `metadata["resource"]` but never `metadata["provenance"]`. The fixture helper always passes `provenance=[]`, so a cross-Source provenance swap would not be caught. (`tests/unit/cli/test_backfill_source_titles.py:449-478`)

11. **Spec scenario 12 untested end-to-end**: "An unrewritable `title:` scalar is skipped, not overwritten" has no end-to-end test through the resolver or CLI with a block-scalar fixture. Only the unit-level `_patch_title_line` guard is tested. Same gap as finding #8.

12. **Spec scenario 2 untested at runtime**: "The command accepts no concept-id argument" is true by code inspection (signature has only `--auto` option, no positional parameter), but no CliRunner test passes an extra positional argument and asserts Typer's rejection. (`main.py:3774-3780`)

All twelve are independent of the correctness of the shipped code. They are gaps in test coverage, message clarity, or defensive guards, not functional defects. Each should be filed with its specific file:line references so future work can address them with clear scope.

## Traceability

**Engram artifacts** (hybrid mode persists to both):
- `sdd/source-title-backfill/proposal` — proposal.md
- `sdd/source-title-backfill/spec` — spec.md
- `sdd/source-title-backfill/design` — design.md
- `sdd/source-title-backfill/tasks` — tasks.md
- `sdd/source-title-backfill/explore` — explore.md
- `sdd/source-title-backfill/verify-report` — verify-report.md
- `sdd/source-title-backfill/archive-report` — archive-report.md (this file)

**Filesystem artifacts** (archived at `openspec/changes/archive/2026-08-01-source-title-backfill/`):
- `proposal.md`
- `design.md`
- `tasks.md` (all 37 items ticked)
- `specs/source-title-backfill/spec.md` (merged delta → main spec)
- `explore.md`
- `verify-report.md`
- `archive-report.md` (this file)

**Main spec**: `openspec/specs/source-title-backfill/spec.md` (newly created, full spec copied from delta)

**Merged code**:
- Slice 1: `src/openkos/bundle/source_titles.py` (titleize, scan, resolve, retitle), tests in `tests/unit/bundle/test_source_titles.py`
- Slice 2: `relabel_index_entry` in `src/openkos/bundle/index.py`, tests in `tests/unit/bundle/test_index.py`
- Slice 3: `backfill_source_titles_cmd` in `src/openkos/cli/main.py:3773-3944`, tests in `tests/unit/cli/test_backfill_source_titles.py`

All in `main` @ `7e2ad47` (PR #304 merge commit).

## Decisions Held

**Decision 1 — Log history is a record, never rewritten**: Confirmed in code. Only `insert_log_entry` is called; `remove_log_entry` is never used. Historical entries with old titles persist.

**Decision 2 — Bundle-wide only, no concept-id argument**: Confirmed in code. Command signature has only `--auto` option, no positional identifier parameter.

**Decision 3 — Mechanical test uses stem, not slug**: Confirmed in code. `titleize(Path(resource).stem)` comparison at `source_titles.py:216-217`. The `01-Introduction.md` counterexample is pinned by test.

## Rollback

`openkos backfill-source-titles` produces exactly one `_autocommit` per successful run. Rollback is `git revert` of that single commit, or `git checkout` of the affected paths. `raw/` is never written, so inputs are byte-identical after revert and the operation is fully re-runnable. No derived index is touched. No schema migration. Rollback is clean and safe.

## Next Steps

None — the change is complete, verified, and archived. The twelve open findings should each be filed as separate GitHub issues against #298's follow-up chain for future attention, but they do not block this archive or prevent the change from being shipped.

The companion lint check "Source title still equals its slug" (#248's own *What Is Still Owed* list) remains separate follow-up work.

---

**Archived**: 2026-08-01 · **Status**: CLOSED · **Quality**: PASS WITH WARNINGS
