# Verification Report: source-title-backfill

**Change**: `source-title-backfill` · **Verified at**: `main` @ `7e2ad47` (post-delivery, PRs #302, #303, #304 merged) · **Mode**: hybrid

## Verdict: PASS WITH WARNINGS

Full quality gate is green and every spec requirement traces to executing code. 27 of 28 scenarios have a direct covering test; one scenario ("An unrewritable `title:` scalar is skipped, not overwritten") is exercised only at the unit level of its constituent parts (`_patch_title_line` directly, and the `heading-mismatch` catch clause via a *different* trigger — a hand-edited first line), never end-to-end through `resolve_source_title_backfill` with a scalar/anchor/block-title fixture. All three settled product decisions hold in the shipped code. All six non-goals hold. The two-byte-edit acceptance property was verified directly against the shipped Enchiridion example document: exactly 2 changed lines. Ten known open follow-ups were checked one by one against the merged code — all ten are confirmed still present and unfixed; none is stale.

## Gate Results (verbatim)

### `uv run pytest`
```
2931 passed in 87.44s (0:01:27)
```
(Apply-progress's own last recorded run was 2928 passed; the +3 delta is expected drift from unrelated work on `main` since PR #304 merged and is not attributable to this change.)

### `uv run ruff check .`
```
All checks passed!
```

### `uv run ruff format --check .`
```
160 files already formatted
```

### `uv run mypy .`
```
Success: no issues found in 160 source files
```

All four gates: **exit 0, clean.**

## Tasks Completion

All 37 checklist items across the 3 slices in `tasks.md` are ticked. Verified against the disk, not taken on trust:
- Slice 1 (`bundle/source_titles.py`, 1.0–1.14): `titleize`, `retitle_document`, `scan_source_titles`, `resolve_source_title_backfill`, and the four frozen dataclasses all exist at `src/openkos/bundle/source_titles.py` and are covered by `tests/unit/bundle/test_source_titles.py` (19 test functions).
- Slice 2 (`relabel_index_entry`, 2.1–2.5): exists at `src/openkos/bundle/index.py:255-307`, plus the review-driven `_reject_markdown_link_delimiters` guard (task 3.4b) at `index.py:236-252`. Covered by 8 test functions in `tests/unit/bundle/test_index.py`.
- Slice 3 (CLI verb, 3.1–3.18): `backfill-source-titles` exists at `src/openkos/cli/main.py:3773-3944`, registered in the Typer app, covered by 18 test functions in `tests/unit/cli/test_backfill_source_titles.py`.

No unticked task found. No task claims contradicted by the disk state.

## Spec Scenario Compliance Matrix

27 of 28 scenarios: **PASS** (test exists, runs, passes). 1 scenario: **PARTIAL** (constituent logic tested, end-to-end path untested).

| # | Scenario | Covering test(s) | Status |
|---|---|---|---|
| 1 | Only type-source concepts are considered | `test_scan_source_titles_only_considers_type_source_concepts` | PASS |
| 2 | The command accepts no concept-id argument | *(structural: `backfill_source_titles_cmd`'s only parameter is `--auto`; no test drives an extra positional argument through the CLI and asserts Typer's rejection)* | WARNING — untested at runtime, true by inspection (`main.py:3774-3780`) |
| 3 | Malformed resource is warned and never staged | `test_scan_source_titles_warns_on_malformed_resource` (pure); `test_malformed_resource_is_warned_and_never_staged` (CLI) | PASS |
| 4 | A curated title is skipped, not staged | `test_scan_source_titles_curated_vs_candidate` (row 1) | PASS |
| 5 | A mechanical title with a differing derivation is staged | `test_resolve_source_title_backfill_stages_a_differing_derivation` | PASS |
| 6 | A `None` re-derivation stages nothing | `test_resolve_source_title_backfill_files_every_non_staging_reason` (`no-derivable-title` row) | PASS |
| 7 | An identical re-derivation stages nothing | same test (`already-current` row) | PASS |
| 8 | `01-Introduction.md` counterexample classifies as mechanical | `test_scan_source_titles_curated_vs_candidate` (row 2, explicit counterexample) | PASS |
| 9 | A hand-edited first line is refused, not overwritten | `test_retitle_document_refuses_a_mismatch`; `test_resolve_source_title_backfill_files_every_non_staging_reason` (`heading-mismatch` row); `test_hand_edited_first_line_is_refused_not_overwritten` (CLI) | PASS |
| 10 | A matching first line is overwritten normally | `test_retitle_document_patches_only_two_lines_in_a_non_canonical_document` | PASS |
| 11 | Only title and first line change | `test_retitle_document_patches_only_two_lines_in_a_non_canonical_document`; `test_retitle_document_preserves_trailing_body_whitespace` | PASS |
| 12 | An unrewritable `title:` scalar is skipped, not overwritten | `test_retitle_document_fails_closed_on_unrewritable_title` calls `_patch_title_line` **directly**, not `retitle_document`, and not through `resolve_source_title_backfill`. No test feeds a block-scalar/anchor/multi-line `title:` through the full pipeline and asserts it lands in `warned` | **PARTIAL — see Issues** |
| 13 | The index bullet label reflects the new title | `test_relabel_index_entry_rewrites_only_the_matching_bullet_label`; `test_index_bullet_relabeled_and_unstaged_bullets_untouched` (CLI) | PASS |
| 14 | Unstaged Sources' index bullets are untouched | `test_index_bullet_relabeled_and_unstaged_bullets_untouched` | PASS |
| 15 | `raw/` bytes are untouched | `test_invariants_preserved_across_a_confirmed_run` | PASS |
| 16 | Historical `log.md` entries keep the old title | `test_invariants_preserved_across_a_confirmed_run` | PASS |
| 17 | Slug, filename, and Concept ID never change | `test_invariants_preserved_across_a_confirmed_run` | PASS |
| 18 | The derived-index databases are untouched | `test_invariants_preserved_across_a_confirmed_run` | PASS |
| 19 | A fully curated or warned bundle is a no-op | `test_fully_curated_or_warned_bundle_is_a_no_op` | PASS |
| 20 | A bundle with no Sources is a no-op | `test_bundle_with_no_sources_is_a_no_op` | PASS |
| 21 | Preview shows all three buckets before any prompt | `test_preview_shows_all_three_buckets_before_any_prompt` | PASS |
| 22 | `--auto` skips the prompt only | `test_auto_skips_the_prompt_only` | PASS |
| 23 | `review: false` skips the prompt like `--auto` | `test_review_false_skips_the_prompt_like_auto` | PASS |
| 24 | Non-TTY without `--auto` refuses to write | `test_non_tty_without_auto_refuses` | PASS |
| 25 | Declining the prompt performs no write | `test_declining_the_prompt_performs_no_write` | PASS |
| 26 | Immediate re-run after a successful sweep is a no-op | `test_immediate_rerun_after_a_successful_sweep_is_a_no_op` | PASS |
| 27 | A mid-sweep write failure names the paths that already landed | `test_mid_sweep_write_failure_names_the_landed_paths` | PASS |
| 28 | A multi-Source run produces one log entry and one commit | `test_multi_source_run_produces_one_log_entry_and_one_commit`; `test_write_order_is_index_then_sources_then_log` | PASS |

## Settled Product Decisions — Verified Against Code

1. **`log.md` history is NOT retroactively rewritten.** Confirmed: `backfill_source_titles_cmd` only calls `bundle_log.insert_log_entry` (`main.py:3868-3872`); `bundle/log.py`'s `remove_log_entry` is never called from this command. `test_invariants_preserved_across_a_confirmed_run` proves a pre-existing entry carrying the old title survives unchanged.
2. **The verb is bundle-wide with no concept-id argument.** Confirmed: `backfill_source_titles_cmd(auto: bool = ...)` (`main.py:3773-3780`) has exactly one parameter, an option, no positional argument.
3. **The curated test is `title == titleize(Path(resource).stem)`, not the lossy slug variant.** Confirmed at `src/openkos/bundle/source_titles.py:216-217`: `stem = PurePosixPath(resource).stem; if current_title != titleize(stem)`. This operates on the resource's own stem, never on a lowercased slug. Pinned by the `01-Introduction.md` counterexample test (scenario 8 above), which is the exact case issue #298's original (rejected) proposal would have misclassified.

**All three hold.**

## Non-Goals — Verified Against Code

- **No slug/filename/Concept ID rename**: `retitle_document` only patches the frontmatter `title:` line and the first body line (`source_titles.py:92-136`); `resolve_source_title_backfill` carries `concept_id` through unchanged. Confirmed by `test_invariants_preserved_across_a_confirmed_run`.
- **No `.openkos/*.db` rebuild**: no reference to `fts.db`, `vectors.db`, or `graph.db` anywhere in `source_titles.py`, `index.py`'s new code, or `backfill_source_titles_cmd`. Confirmed by the same invariants test (byte-identical `.openkos/*.db` stubs).
- **No `ingest` behavior change**: `_titleize` in `main.py` is now a one-line delegation to `source_titles.titleize`; `tests/unit/cli/test_ingest.py`'s regression test (task 1.3) proves the fallback path behaves identically post-delegation.
- **No `log.md` history rewrite**: see decision 1 above.
- **No single-concept mode**: see decision 2 above.

**All five non-goals hold** (the sixth non-goal, the companion lint check, is explicitly out of scope and not attempted — correctly).

## Acceptance Diff — `retitle_document` on the Shipped Enchiridion Source

Ran directly against `examples/good-life-demo/bundle/sources/notes-on-the-enchiridion-2026-07-05.md`:

```diff
--- before
+++ after
@@ -1,6 +1,6 @@
 ---
 type: Source
-title: Reading notes — Enchiridion, 2026-07-05
+title: Reading Notes Enchiridion 2026 07 05
 description: First pass through Epictetus's Enchiridion and its introduction.
 resource: raw/notes-on-the-enchiridion-2026-07-05.txt
 tags: [reading-notes, philosophy]
@@ -11,7 +11,7 @@
 sensitivity: private
 ---
 
-# Reading notes — Enchiridion, 2026-07-05
+# Reading Notes Enchiridion 2026 07 05
 
 Notes from a first pass through Epictetus's *Enchiridion* and the translator's introduction. They cover the dichotomy of control, the Stoic/Epicurean contrast the introduction draws, and a first reading of *apatheia* as "indifference to emotion" — flagged at the time as something to ask about, and later corrected.
```

**Exactly 2 changed lines.** `description`, `## Source content`, tags, timestamp, and every other frontmatter key/body line are byte-identical.

**Accuracy note (not a defect, a scope clarification)**: this Source's shipped repository does not contain a `raw/` directory, so the actual re-derivation step (`derive_source_title` against real raw bytes) could not be exercised against this example — `retitle_document` was called directly with a synthetic `new_title` to demonstrate the two-edit property, which is exactly what `retitle_document`'s own unit tests already do. Separately: this Source's own on-disk title ("Reading notes — Enchiridion, 2026-07-05") does **not** equal `titleize(Path(resource).stem)` ("notes on the enchiridion 2026 07 05") — under the real `scan_source_titles` classifier this Source would land in the **curated** bucket and never reach `retitle_document` in an actual CLI run. This does not contradict the acceptance claim (the property under test is `retitle_document`'s own two-edit behavior, which is classifier-independent), but it means this specific example is not a live end-to-end demonstration of the full sweep.

## CLI Registration

Confirmed: `openkos --help` lists `backfill-source-titles`; `openkos backfill-source-titles --help` renders correctly with `--auto` as its only option and no positional arguments.

## Follow-Up Confirmation (all ten, checked against merged code — do not fix)

| # | Follow-up | Status | Location |
|---|---|---|---|
| 1 | A post-confirm write replays the pre-confirm snapshot; a mid-prompt edit is overwritten | **CONFIRMED PRESENT** | `src/openkos/cli/main.py:3815-3836` (Phase A snapshot/derivation, all before the confirm gate at 3892-3904) and `:3906-3924` (Phase B write uses the pre-computed `retitle.content`/`new_index_text`/`new_log_text`, never re-read after confirm) |
| 2 | A failure on the final `log.md` write is the one partial state a re-run cannot repair; re-run reports clean success | **CONFIRMED PRESENT** | `src/openkos/cli/main.py:3915-3924` (write order) combined with `src/openkos/bundle/source_titles.py:216-223` (the curated check re-classifies an already-retitled Source as `curated` on re-run, since its new title generally no longer equals `titleize(stem)`, so it is never revisited and the missing log entry is never repaired) |
| 3 | The confirm preview does not disclose that `index.md` and `log.md` will be rewritten | **CONFIRMED PRESENT** | `src/openkos/cli/main.py:3881-3890` — the preview loop lists only staged/skipped/warned Source documents, never mentions `index.md` or `log.md` |
| 4 | `relabel_index_entry`'s relabel count is discarded | **CONFIRMED PRESENT** | `src/openkos/cli/main.py:3856-3858` — `new_index_text, _ = bundle_index.relabel_index_entry(...)`; the `int` count is bound to `_` and never checked, so a staged Source with no catalog bullet still reports success |
| 5 | `resolve_source_title_backfill` files every new refusal shape under `warned`/`heading-mismatch` | **CONFIRMED PRESENT** | `src/openkos/bundle/source_titles.py:296-302` — a bare `except ValueError` around `retitle_document(...)` labels every refusal (hand-edited first line AND any frontmatter-shape refusal from `_patch_title_line`) as `heading-mismatch` |
| 6 | A derived title >80 chars is wrapped into two lines by the YAML dumper, adding a third changed line; `_TITLE_MAX_CHARS` is 120 | **CONFIRMED PRESENT** — reproduced live | `src/openkos/bundle/source_titles.py:86-89` (`_patch_title_line`'s `new_line` via `okf.dump_frontmatter`); `_TITLE_MAX_CHARS = 120` at `src/openkos/source_title.py:209`. Reproduced: a 124-char, space-containing title fed through `okf.dump_frontmatter` wraps onto two YAML lines |
| 7 | The trailing-YAML-comment guard in `_patch_title_line` is a heuristic with a false-negative edge | **CONFIRMED PRESENT** — reproduced live | `src/openkos/bundle/source_titles.py:78-84` — the guard checks literal `" #"` (space+hash); a quoted scalar's comment needs no preceding space (`title: "foo"#comment` parses to `foo`, silently dropping the comment, per PyYAML), which the guard's substring check does not catch |
| 8 | `retitle_document` has no test proving it PROPAGATES a `_patch_title_line` refusal; the refusal rows call the private helper directly | **CONFIRMED PRESENT** | `tests/unit/bundle/test_source_titles.py:118-131` — `test_retitle_document_fails_closed_on_unrewritable_title` calls `source_titles._patch_title_line(...)` directly, never `retitle_document(...)` or `resolve_source_title_backfill(...)`. This is the same gap as spec scenario 12 above |
| 9 | The malformed-catalog refusal test snapshots only two paths, not the whole workspace | **CONFIRMED PRESENT** | `tests/unit/cli/test_backfill_source_titles.py:481-505` — `test_a_malformed_index_refuses_before_any_write` only captures `source_before`/`log_before`, unlike the sibling `test_a_confirmed_run_touches_only_the_expected_paths` (line 508+) which explicitly snapshots the whole workspace and states in its own docstring why that is necessary |
| 10 | The success-path read-back asserts `resource` but not provenance, which is empty in the fixture | **CONFIRMED PRESENT** | `tests/unit/cli/test_backfill_source_titles.py:449-478` — `test_each_retitled_document_receives_its_own_rewritten_bytes` asserts `metadata["resource"]` but never `metadata["provenance"]`; the `_write_source` helper (line 48-70) always passes `provenance=[]`, so a cross-Source provenance swap would not be caught even if the assertion were added without also fixing the fixture |

**All ten follow-ups are confirmed present and unfixed. None is stale.**

## Design Coherence

All D1–D7 decisions in `design.md` are reflected in the shipped code:
- D1 (module placement, layering): confirmed — `bundle/source_titles.py` imports only `openkos.model.okf`, `openkos.source_title`, `pathlib.PurePosixPath`, `yaml`, stdlib.
- D2 (raw text injected, two-pass core): confirmed — `scan_source_titles`/`resolve_source_title_backfill` split exactly as designed.
- D3 (dataclass shapes, closed reason vocabularies): confirmed — all reason tokens (`resource-missing`, `resource-malformed`, `curated`, `no-derivable-title`, `empty-raw-source`, `already-current`, `raw-unreadable`, `raw-undecodable`, `heading-mismatch`) appear exactly as specified.
- D4 (surgical patch, post-review revision): confirmed — `_split_frontmatter_verbatim`/`_patch_title_line` implement the revised surgical approach, not the original load-and-dump design. CRLF documents are refused, matching the revision.
- D5 (`relabel_index_entry`): confirmed, plus the review-driven `_reject_markdown_link_delimiters` addition (task 3.4b) not in the original design but tracked in tasks.md.
- D6 (index-first write order): confirmed at `main.py:3906-3924`, with the inline comment present as designed.
- D7 (no ADR): consistent with what shipped — no new schema, no new persisted state.

No design deviation found beyond the two already self-documented in `apply-progress.md` (the CRLF-unreachability finding under D4, and task 1.14's post-review revision) — both are accurately described there.

## Issues

### CRITICAL
None.

### WARNING
1. **Spec scenario 12 / Follow-up 8 — untested end-to-end path**: "An unrewritable `title:` scalar is skipped, not overwritten" has no test driving a block-scalar/anchor/multi-line `title:` fixture through `resolve_source_title_backfill` (or the CLI) and asserting it lands in `warned`/`heading-mismatch`. The unit-level guard (`_patch_title_line`) is solidly tested; the propagation through the `except ValueError` catch at `source_titles.py:300-302` is only exercised via a *different* trigger (hand-edited first line). Recommend adding one parametrized case reusing the existing `_patch_title_line` fixture rows through `resolve_source_title_backfill`.
2. **Spec scenario 2 — no runtime test for "accepts no positional argument."** True by code inspection but never asserted by a CliRunner invocation with an extra argument.

### SUGGESTION
The ten follow-ups above (relabel count discarded, YAML 80-char wrap, comment-guard false negative, preview non-disclosure, log.md-failure asymmetry, pre-confirm-snapshot staleness, provenance untested, malformed-catalog test's narrow snapshot) should each be filed as a GitHub issue against #298's follow-up chain rather than fixed here — all ten were deliberately deferred across four review rounds per the task brief, and remain accurately deferred.

## Skipped Verification Dimensions

None — proposal, spec, design, tasks, and apply-progress were all present and all were used.

## Artifacts

- `openspec/changes/source-title-backfill/verify-report.md` (this file)
- Engram: `sdd/source-title-backfill/verify-report`
