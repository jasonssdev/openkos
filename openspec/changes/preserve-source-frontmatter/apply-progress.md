# Apply Progress: preserve-source-frontmatter

Refs #1062. Tasks: `tasks.md`. Design: `design.md`. Mirror: Engram
`sdd/preserve-source-frontmatter/apply-progress`.

## Slice 1 (PR 1 → `main`): Phase 1 — Parse seam + ADR-0030

**Status**: COMPLETE — 23/23 tasks done (1.1-1.23). Strict TDD mode, runner
`uv run pytest`.

**Scope**: the fail-closed incoming-frontmatter parser in `model/okf.py`,
the shared frontmatter-boundary rule used by both `source_title` and the
parser, and ADR-0030 (already staged from the planning session at
`92df2c0`; tasks 1.18/1.19 required no new work here).

### Completed Tasks

- [x] 1.1 `test_frontmatter_end_moved_to_okf_module` (identity pin)
- [x] 1.2 `test_frontmatter_block_end_parity_table` (redesigned — see Deviations)
- [x] 1.3 `okf.frontmatter_block_end` (moved verbatim); `source_title`
      rebinds `_frontmatter_end` to it via plain assignment
- [x] 1.4 `test_parse_absent_cases`
- [x] 1.5 `test_parse_empty_cases`
- [x] 1.6 `test_parse_too_large_boundary` (split into two literal tests)
- [x] 1.7 `test_parse_alias_cases` (split into three; narrowed to
      AliasEvent-only per task wording — see Deviations)
- [x] 1.8 `test_parse_too_deep_boundary` (split into two)
- [x] 1.9 `test_parse_malformed_cases`
- [x] 1.10 `test_parse_not_a_mapping_cases`
- [x] 1.11 `parse_incoming_frontmatter` checks 1-8
- [x] 1.12 `test_parse_unsupported_value_cases`
- [x] 1.13 `test_parse_round_trip_gate_*` (split into three)
- [x] 1.14 `_is_plain_data` + round-trip gate (checks 9-10)
- [x] 1.15 `test_parse_never_raises` (found and fixed a real gap — see
      Issues Found)
- [x] 1.16 deferred to Phase 2 with an explicit note added to `tasks.md`
      (the call site does not exist until 2.5-2.8)
- [x] 1.17 satisfied by the same deferral note
- [x] 1.18 ADR-0030 already existed at session start (staged in `92df2c0`)
- [x] 1.19 ADR-0030 README index row already existed
- [x] 1.20 `ruff check` / `ruff format --check` / `mypy` — green
- [x] 1.21 focused + full `pytest --cov` — green, 96.92% coverage (gate 90%)
- [x] 1.22 `evals/run_self_tests.py` — 44/44 green
- [x] 1.23 committed — see Commits

### Files Changed

| File | Action | What |
|---|---|---|
| `src/openkos/model/okf.py` | Modified | `SOURCE_FRONTMATTER_KEY`, `INCOMING_FRONTMATTER_MAX_BYTES`, `INCOMING_FRONTMATTER_MAX_DEPTH`, `IncomingFrontmatterStatus`, `IncomingFrontmatter`, `frontmatter_block_end`, `_incoming_frontmatter_block`, `_incoming_frontmatter_prescan`, `parse_incoming_frontmatter`, `_is_plain_data` |
| `src/openkos/source_title.py` | Modified | deleted `_frontmatter_end`'s own body; rebinds to `okf.frontmatter_block_end` via plain assignment (not an aliased import, to satisfy mypy strict's implicit-reexport check); docstring updated |
| `tests/unit/test_source_title.py` | Modified | task 1.1/1.2 tests added |
| `tests/unit/model/test_okf_incoming_frontmatter.py` | Created | all parser/domain/round-trip tests (34 tests) |
| `openspec/changes/preserve-source-frontmatter/tasks.md` | Modified | 1.1-1.23 marked `[x]`; deferral note added to Phase 2 for task 1.16 |
| `openspec/changes/preserve-source-frontmatter/apply-progress.md` | Created | this file |

### TDD Cycle Evidence

| Task(s) | Test file | RED (observed) | GREEN | Mutation |
|---|---|---|---|---|
| 1.1/1.2 | `test_source_title.py` | `AttributeError: module 'openkos.model.okf' has no attribute 'frontmatter_block_end'` (10 tests) | 109/109 passed after 1.3 | boundary condition `!=` → `.strip() !=`: `trailing_space_after_dashes` row flipped 0→3; BOM row unaffected (documented — `﻿` is not `str.isspace()`) |
| 1.4-1.10 | `test_okf_incoming_frontmatter.py` | `AttributeError: ... no attribute 'parse_incoming_frontmatter'` (22 tests) | 22/22 after 1.11 | byte cap `>` → `>=`: exact-limit row flipped `parsed`→`too-large`; alias check removed: billion-laughs/self-reference flipped `alias`→`parsed` |
| 1.12/1.13 | `test_okf_incoming_frontmatter.py` | 9 failures (domain/round-trip not wired) + 1 unrelated genuine bug found (surrogate `UnicodeEncodeError`) | 34/34 after 1.14 + bug fix | round-trip gate removed (monkeypatched `dump_frontmatter`): flipped `unsupported-value`→`parsed` |
| 1.15 | `test_okf_incoming_frontmatter.py` | `lone_surrogate_escape` case raised `UnicodeEncodeError` from the byte-size check — a REAL gap, not a vacuous pass | fixed by wrapping `block.encode("utf-8")` in `try/except UnicodeEncodeError → "malformed"` | N/A (bugfix, not a mutation-check task) |

All __pycache__ purged before each verdict; every mutation reverted with
the exact inverse edit.

### Deviations from Design

1. **Task 1.2's parity test redesigned to avoid circularity.** After task
   1.1's rebinding, `source_title._frontmatter_end IS okf.frontmatter_block_end`
   (the same object), so comparing their outputs to each other is exactly
   the circular shape `tasks.md`'s own header warns against ("a property
   test whose two sides call the same function under test is circular").
   Implemented instead as a table of literal expected indices for
   `okf.frontmatter_block_end`, paired with literal expected titles from
   `source_title.derive_source_title`'s real public output (using
   marker/decoy H1 lines so a wrong boundary would surface a different,
   also-literal title) — both sides checked against hand-computed
   literals, never against each other. This still fulfills the edge-case
   table and the mutation-sensitivity requirement (confirmed: the
   trailing-space row flips under the example mutation).
2. **Task 1.7's alias guard narrowed to `AliasEvent` only, not "any
   non-`None` anchor."** design.md Decision 1 check 3's literal wording
   ("any event with a non-`None` anchor, or any `AliasEvent`") would reject
   task 1.7's own "lone anchor with no alias" case, which the task
   explicitly requires to parse successfully. Implemented per the task's
   explicit, later-written acceptance criterion: only an actual
   `AliasEvent` triggers rejection. This still rejects both the
   billion-laughs and self-referencing cases (both contain real
   `AliasEvent`s) while letting a harmless, unreferenced anchor through.
   Recommend design.md's Decision 1 table be corrected to match in a
   follow-up.
3. **Task 1.9's "two YAML documents separated by `...`" case redesigned.**
   An inner `---` inside the test block is swallowed by
   `_incoming_frontmatter_block`'s own boundary rule (any line equal to
   `"---"` closes the block first), so a literal second `---`-delimited
   document is unreachable through the public `parse_incoming_frontmatter`
   entry point. Verified empirically that PyYAML's event parser always
   raises `ParserError` before emitting a second `DocumentStartEvent` for
   any implicit-restart variant reachable this way. Used the implicit-
   restart form (`"a: 1\n...\nb: 2"`, no second fence) instead, which
   raises during the pre-scan and is caught by the broad
   `except Exception → "malformed"` handler — same asserted outcome
   (`status="malformed"`), reached via the exception path rather than the
   `document_starts > 1` counter. That counter is kept in the
   implementation as documented defensive code (design fidelity, in case
   the boundary rule ever changes) but is effectively unreachable via the
   public API today; this is noted in its own docstring.
4. **Task 1.16 deferred to Phase 2**, per the task's own explicit choice
   between writing an `xfail(strict=True)` now or deferring with a note —
   deferred, since the call site
   (`compose_source_document → okf.parse_incoming_frontmatter`) does not
   exist until Phase 2 wires it. A note was added to `tasks.md`'s Phase 2
   section naming the exact test to add and which guard to reuse, per task
   1.17's instruction not to let this be silently dropped.

### Issues Found

- **Real gap caught by task 1.15's regression test, not a vacuous pass**:
  the byte-size check (`len(block.encode("utf-8"))`) raised
  `UnicodeEncodeError` for a block containing a lone surrogate escape
  (`"\udcff"`), which is exactly the scenario the task's docstring predicts
  ("if RED, a raise path was missed... and must be closed there"). Fixed
  by wrapping the encode call in `try/except UnicodeEncodeError`, mapping
  to `status="malformed"`. This was not one of the ten named checks in
  design.md's table; it is a defensive addition the task's own regression
  test required.
- Minor, non-blocking: `okf.py` lines 776-777 (the round-trip gate's
  `except Exception` branch) are not covered by any test — only the
  `!=`-mismatch path (via the monkeypatch test) is exercised, not a genuine
  exception during `dump_frontmatter`/`load_frontmatter`. Coverage gate
  (90%) is met regardless (96.92% overall); flagging for a future slice if
  100% branch coverage on this function specifically becomes a goal.

### Review Workload / Size

Actual authored changed lines for this work unit: **657 insertions + 23
deletions across 4 files** (`git diff --shortstat 92df2c0..HEAD`, excluding
the second, docs-only commit). This exceeds the review budget (400) and
design.md's own forecast for Slice 1 (~300-450). Phase 1 is defined as one
indivisible PR in `tasks.md`'s Suggested Work Units table (PR 1 = Phase 1 in
full); the parser's ten-check fail-closed table and its mutation-proof
tests do not split further without breaking either the shared-boundary
guarantee or the "every hostile fixture asserts its own status" testing
contract. Per session config, `size:exception` is invoked for this slice
(owner pre-approved this outcome for unsplittable slices); no attempt was
made to shrink the diff by cutting tests, comments, or docstrings to fit
the number.

### Commits

1. `96db82f` — `feat(okf): parse and validate incoming source frontmatter, fail-closed (#1062)`
   (code + all tests; 657 insertions, 23 deletions, 4 files)
2. (this commit) — `docs(sdd): record frontmatter slice 1 progress (#1062)`
   (`tasks.md` checkbox updates + this file)

### Next

All Phase 1 tasks complete. `next_recommended: sdd-archive` (verification
optional per the SDD contract) or `sdd-apply` again for Phase 2 (Verbatim
preserve, PR 2 → `main`, after this PR merges).

## Slice 2 (PR 2 → `main`, after PR 1 merges): Phase 2 — Verbatim preserve

**Status**: COMPLETE — 25/25 tasks done (2.1-2.25). Strict TDD mode, runner
`uv run pytest`. Branch `feat/1062-frontmatter-p2`, stacked on the
not-yet-merged slice-1 branch (PR #1088) at `3888fb9`.

**Scope**: `source_frontmatter` emission in `build_source_concept`;
`compose_source_document` wiring for BOTH `build_source_concept` call sites
(the fresh build and `compose_catalog_update`'s conditional rebuild);
`source_frontmatter` in `build_merged_document`'s `_SPECIAL_KEYS`;
`lift_changed` (frontmatter delta only) gating the CLI's #773 convergence
skip; the Source-only-rewrite preview line; the `migrate_document`
`Unchanged` pin; task 1.16's deferred call-site contract test.

### Completed Tasks

- [x] 2.1-2.3 `test_build_source_concept_emits_source_frontmatter_when_given`,
      `..._omits_source_frontmatter_when_none`,
      `..._no_anchor_or_alias_when_tags_share_values_with_source_frontmatter`
- [x] 2.4 `build_source_concept(source_frontmatter=...)`, `copy.deepcopy`d
- [x] 2.5-2.7 `test_compose_source_document_parses_and_forwards_frontmatter`,
      `..._malformed_frontmatter_lifts_nothing`,
      `..._frontmatter_free_is_byte_identical`
- [x] 2.8 `compose_source_document` calls `okf.parse_incoming_frontmatter`
      behind the SAME guard as `derive_source_title` (task 1.16's deferred
      test added here, per the Phase 1 deferral note)
- [x] 2.9 `test_compose_catalog_update_second_build_carries_source_frontmatter`
- [x] 2.10 `SourceDocumentPlan.source_frontmatter`; `compose_catalog_update`'s
      conditional rebuild passes `source_frontmatter=source.source_frontmatter`
- [x] 2.11-2.12 `test_reingest_converged_source_with_new_frontmatter_triggers_rewrite`,
      `..._with_unchanged_frontmatter_stays_converged`
- [x] 2.13 `SourceDocumentPlan.lift_changed` (frontmatter delta this slice);
      CLI skip condition gains `and not source_plan.lift_changed`
- [x] 2.14 `test_source_only_rewrite_preview_names_recorded_frontmatter`
- [x] 2.15 `source frontmatter recorded ({n} key(s))` preview line, guarded
      on `converged is not None and source_plan.lift_changed`
- [x] 2.16-2.17 `test_build_merged_document_source_frontmatter_survivor_only`,
      `..._survivor_wins` (2.17 passed vacuously — regression pin)
- [x] 2.18 `SOURCE_FRONTMATTER_KEY` added to `build_merged_document`'s
      `_SPECIAL_KEYS`
- [x] 2.19 `test_unmerge_restores_absorbed_source_frontmatter` — passed on
      first run (unmerge restores from the full-document `merged_from`
      ledger snapshot, not field-by-field reconstruction — confirmed the
      case task 2.19 flagged as possible; test kept as the regression pin)
- [x] 2.20 `test_migrate_document_unchanged_with_nested_engine_owned_keys_in_source_frontmatter`
      — passed on first run (`migrate_document`'s rules already read only
      top-level keys; test-only, no implementation change)
- [x] 2.21 `test_read_concept_never_discloses_source_frontmatter` — passed
      on first run (`ConceptRecord`'s curated field set already excludes
      `source_frontmatter`; test-only, no implementation change)
- [x] 2.22 `ruff check` / `ruff format --check` / `mypy` — green (one mypy
      fix needed in the new migrate_document test: `isinstance(nested, dict)`
      narrowing before subscripting an `object`-typed value)
- [x] 2.23 focused command green (702 passed); full `pytest --cov` green —
      7098 passed, 2 skipped, 96.92% coverage (gate 90%)
- [x] 2.24 `evals/run_self_tests.py` — 44/44 green
- [x] 2.25 committed — see Commits

### Files Changed

| File | Action | What |
|---|---|---|
| `src/openkos/model/okf.py` | Modified | `import copy`; `build_source_concept(source_frontmatter=...)`, `copy.deepcopy`d emission; `SOURCE_FRONTMATTER_KEY` added to `build_merged_document`'s `_SPECIAL_KEYS` |
| `src/openkos/application/ingest.py` | Modified | `_read_source_frontmatter` helper; `compose_source_document` parses incoming frontmatter behind the existing blank/non-UTF-8 guard, computes `source_frontmatter`/`lift_changed`, forwards `source_frontmatter` to `build_source_concept`; `SourceDocumentPlan` gains `source_frontmatter`/`lift_changed`; `compose_catalog_update`'s conditional rebuild call carries `source_frontmatter=` |
| `src/openkos/cli/main.py` | Modified | #773 skip condition gains `and not source_plan.lift_changed`; Source-only-rewrite preview gains the `source frontmatter recorded (N key(s))` line |
| `tests/unit/model/test_okf.py` | Modified | 2.1-2.3, 2.16-2.17 tests |
| `tests/unit/application/test_ingest.py` | Modified | 2.5-2.7, 2.9, and the deferred-1.16 call-site-contract test |
| `tests/unit/cli/test_ingest.py` | Modified | 2.11-2.12, 2.14 tests |
| `tests/unit/cli/test_merge_roundtrip.py` | Modified | 2.19 unmerge round-trip test |
| `tests/unit/model/test_okf_migrate_document.py` | Modified | 2.20 `Unchanged` pin |
| `tests/unit/application/test_concept_read.py` | Modified | 2.21 MCP non-disclosure regression pin |
| `openspec/changes/preserve-source-frontmatter/tasks.md` | Modified | 2.1-2.25 marked `[x]` |
| `openspec/changes/preserve-source-frontmatter/apply-progress.md` | Modified | this section |

### TDD Cycle Evidence

| Task(s) | Test file | RED (observed) | GREEN | Mutation |
|---|---|---|---|---|
| 2.1-2.3 | `test_okf.py` | `TypeError: build_source_concept() got an unexpected keyword argument 'source_frontmatter'` (2.1, 2.3); 2.2 written to fail via explicit `source_frontmatter=None` for the same reason | 3/3 after 2.4 | removing `copy.deepcopy` (kept plain assignment) flipped 2.3's anchor test to fail with a literal `&id001`/`*id001` pair in the dumped text; restored with the exact inverse edit |
| 2.5-2.7 | `test_ingest.py` (application) | 2.5: `KeyError: 'source_frontmatter'`; 2.6/2.7 passed vacuously (predicted — no call site existed yet) | 2.5-2.7 GREEN after 2.8 | N/A (no mutation task assigned to 2.5-2.7) |
| deferred 1.16 | `test_ingest.py` (application) | `assert 0 == 1` on the PRECONDITION (spy never fired for ordinary content, since the call site didn't exist) | GREEN after 2.8 | N/A |
| 2.9 | `test_ingest.py` (application) | `KeyError: 'source_frontmatter'` on `source.content` itself (before 2.10 even the first build lacked the field access path); after 2.8 alone, RED became the rebuild's frontmatter staying absent | GREEN after 2.10 | reverting only the second call site's `source_frontmatter=` argument reproduced `assert None == {'author': 'Jane'}`; restored with the exact inverse edit |
| 2.11 | `test_ingest.py` (cli) | precondition passed (plain re-ingest converges); final assertion `KeyError: 'source_frontmatter'` before 2.13 | GREEN after 2.13 | N/A (no mutation task assigned) |
| 2.12 | `test_ingest.py` (cli) | passed vacuously (predicted) | stayed GREEN after 2.13 | N/A |
| 2.14 | `test_ingest.py` (cli) | `AssertionError: assert 'source frontmatter recorded (3 key(s))' in ''` | GREEN after 2.15 | N/A |
| 2.16 | `test_okf.py` | `AssertionError: assert 'source_frontmatter' not in {...}` (absorbed value crossed the merge) | GREEN after 2.18 | N/A |
| 2.17 | `test_okf.py` | passed vacuously (predicted) | stayed GREEN after 2.18 | N/A |
| 2.19 | `test_merge_roundtrip.py` | passed on first run (regression pin — full-snapshot restore) | N/A | N/A |
| 2.20 | `test_okf_migrate_document.py` | passed on first run (regression pin) | N/A | N/A |
| 2.21 | `test_concept_read.py` | passed on first run (regression pin) | N/A | N/A |

All `__pycache__` purged before each verdict; every mutation reverted with
the exact inverse edit.

### Deviations from Design

None — implementation matches design.md Decisions 1, 2, 7, 8, 9. Three
tasks (2.19, 2.20, 2.21) landed as pure regression pins with no production
code change, exactly the outcome design.md's own text anticipated for each
("this may already pass", "confirm this and record it as a regression
pin", "confirm this explicitly").

### Issues Found

None.

### Review Workload / Size

Actual authored changed lines for this work unit: **672 insertions + 12
deletions across 9 files** (`git diff --shortstat 3888fb9..470db03`,
excluding the second, docs-only commit). This exceeds the review budget
(400) and
design.md's own forecast for Slice 2 (~300-450). Phase 2 is defined as one
indivisible PR in `tasks.md`'s Suggested Work Units table (PR 2 = Phase 2
in full): splitting further would separate a `[TEST]`/`[IMPL]` pair across
PR boundaries, or separate `_SPECIAL_KEYS`'s merge exclusion from the
builder change it depends on. Per session config, `size:exception` is
invoked for this slice (owner pre-approved this outcome for unsplittable
slices); no attempt was made to shrink the diff by cutting tests, comments,
or docstrings to fit the number.

### Commits

1. `470db03` — `feat(ingest): preserve incoming frontmatter verbatim under source_frontmatter (#1062)`
   (code + all tests; 672 insertions, 12 deletions, 9 files)
2. (this commit) — `docs(sdd): record frontmatter slice 2 progress (#1062)`
   (`tasks.md` checkbox updates + this file)

### Next

All Phase 2 tasks complete. `next_recommended: sdd-archive` (verification
optional per the SDD contract) or `sdd-apply` again for Phase 3 (Source
lift: tags + sensitivity, PR 3 → `main`, after this PR merges) or Phase 4
(date lift, independent of Phase 3, also after this PR merges).

## Slice 3 (PR 3 → `main`, after PR 2 merges): Phase 3 — Source lift: tags + sensitivity

**Status**: COMPLETE — 23/23 tasks done (3.0-3.22). Strict TDD mode, runner
`uv run pytest`. Branch `feat/1062-frontmatter-p3`, stacked on the
not-yet-merged slice-2 branch (PR #1089) at `a3aa483`.

**Scope**: `okf.normalize_tags`/`union_tags`/`IncomingLift`/`NO_LIFT`/
`lift_incoming_frontmatter` (tags + sensitivity halves); stored-tag read
and tag-union wiring; sensitivity fold (raise-only) in
`compose_source_document`; `SourceDocumentPlan.tags`/`frontmatter_changed`/
`tags_added`/`sensitivity_changed`; the `tags added:` preview line and the
`set-sensitivity` raise advisory; the never-lifted-keys regression fixture;
the Decision 4 LLM-send-floor gate confirmation (test-only, post-#1087).

### 3.0 Precondition

PR #1087 (issue #1086) was ALREADY on this branch's history at session
start (commit `77b9643`, confirmed by `git log`), and
`src/openkos/cli/main.py:5458` already reads
`workspace_floor=source_plan.source_sensitivity` (confirmed by direct
`grep`). No rebase was needed; 3.11-3.14 proceeded directly.

### Completed Tasks

- [x] 3.0 precondition confirmed (see above) — no rebase needed
- [x] 3.1-3.3 `TestNormalizeTagsShapeTable`, `TestUnionTagsOrderPreserving`
      (test class names differ from the task's literal
      `test_normalize_tags_shape_table`/`test_union_tags_order_preserving`
      — split into parametrized classes matching Phase 1/2's established
      pattern); `okf.normalize_tags`, `okf.union_tags`
- [x] 3.4-3.5 `TestLiftIncomingFrontmatter` (7 tests, split rather than one
      `test_lift_tags_and_sensitivity_shapes`); `okf.IncomingLift`,
      `okf.NO_LIFT`, `okf.lift_incoming_frontmatter`
- [x] 3.6-3.10 `test_compose_source_document_fresh_ingest_tags_are_exactly_lifted`,
      `..._reingest_tags_are_union`, `..._hand_added_tag_survives_reingest`,
      `TestSensitivityFoldRaiseOnly` (4 tests, split rather than one
      parametrized test); `_read_source_tags` helper; `compose_source_document`
      now folds `lift.tags`/`lift.sensitivity` onto the pre-lift resolved
      state; `SourceDocumentPlan.tags`; `compose_catalog_update`'s second
      build now passes `tags=list(source.tags)` (removes the `tags=[]`
      hard-code Phase 2 deliberately left in place)
- [x] 3.11-3.14 `test_incoming_confidential_declaration_blocks_this_runs_extraction`,
      `test_include_confidential_still_allows_send_past_frontmatter_raised_floor`,
      `test_lower_incoming_sensitivity_does_not_lower_extraction_floor`,
      `test_unrecognized_incoming_sensitivity_also_raises_extraction_floor`
      — ALL 4 passed on FIRST run, confirming tasks-phase decision 1's
      analysis: no gate-specific code change was needed
- [x] 3.15 `TestNeverLiftedKeysLeaveNoLift` (11 keys parametrized) — passed
      on first run, confirming 3.5's closed allow-list does not leak
- [x] 3.16-3.18 4 preview/advisory tests (`preview_names_tags_added`,
      `preview_names_both_frontmatter_and_tags`,
      `raised_sensitivity_advises_set_sensitivity`,
      `that_does_not_raise_sensitivity_prints_no_advisory`) + 3.17's
      regression pin (`event_date_only_prints_neither_new_line`);
      `SourceDocumentPlan` gains `frontmatter_changed`/`tags_added`/
      `sensitivity_changed` (a DEVIATION from design's Interfaces/Contracts
      list — see below); CLI preview gains the `tags added:` line and the
      stderr `set-sensitivity` advisory
- [x] 3.19 `ruff check` / `ruff format --check` / `mypy .` — green (ruff
      format needed one pass on 4 files after the edits)
- [x] 3.20 focused command green (497 passed); full `pytest --cov` green —
      7149 passed, 2 skipped, 96.92% coverage (gate 90%)
- [x] 3.21 `evals/run_self_tests.py` — 44/44 green
- [x] 3.22 committed — see Commits

### Files Changed

| File | Action | What |
|---|---|---|
| `src/openkos/model/okf.py` | Modified | `normalize_tags`, `union_tags`, `IncomingLift`, `NO_LIFT`, `lift_incoming_frontmatter` |
| `src/openkos/application/ingest.py` | Modified | `_read_source_tags` helper; `compose_source_document` folds tags (union) and sensitivity (raise-only) from the lift; `SourceDocumentPlan` gains `tags`, `frontmatter_changed`, `tags_added`, `sensitivity_changed`; `compose_catalog_update`'s second build passes `tags=list(source.tags)` |
| `src/openkos/cli/main.py` | Modified | preview gains `tags added: {a}, {b}` line, gated on `tags_added`; stderr `set-sensitivity` advisory, gated on `sensitivity_changed`; the existing `source frontmatter recorded` line is now gated on `frontmatter_changed` instead of the OR'd `lift_changed` |
| `tests/unit/model/test_okf_incoming_frontmatter.py` | Modified | 3.1-3.5, 3.15 tests |
| `tests/unit/application/test_ingest.py` | Modified | 3.6-3.9 tests |
| `tests/unit/cli/test_ingest.py` | Modified | 3.11-3.13, 3.16-3.17 tests |
| `openspec/changes/preserve-source-frontmatter/tasks.md` | Modified | 3.0-3.22 marked `[x]` |
| `openspec/changes/preserve-source-frontmatter/apply-progress.md` | Modified | this section |

### TDD Cycle Evidence

| Task(s) | Test file | RED (observed) | GREEN | Mutation |
|---|---|---|---|---|
| 3.1-3.2 | `test_okf_incoming_frontmatter.py` | `AttributeError: ... no attribute 'normalize_tags'`/`'union_tags'` (17 tests) | 17/17 after 3.3 | `normalize_tags`: changed the non-string-item check to skip just that item — mixed-list row flipped from `()` to `('alpha','beta')`; `union_tags`: swapped union order — 3 tests flipped |
| 3.4 | `test_okf_incoming_frontmatter.py` | `AttributeError: ... no attribute 'lift_incoming_frontmatter'` (7 tests) | 7/7 after 3.5 | N/A (no mutation task assigned to 3.4) |
| 3.6-3.8 | `test_ingest.py` (application) | `plan.tags`: `AttributeError` (3.6); `AssertionError` on the union value (3.7-3.8) | 3/3 after 3.10 | N/A (no mutation task assigned) |
| 3.9 | `test_ingest.py` (application) | 1/4 failed for the wrong reason initially (byte-identity sub-cases passed vacuously, as predicted in the task text); the 2 RAISE cases failed as `AssertionError` before 3.10 | 4/4 after 3.10 | passing `lift.sensitivity` to `combine_sensitivity` unconditionally (never skipping the fold when absent) flipped the explicit-null case from `'public'` to `'private'` — the exact `_rank(None)` hazard design.md's fold-order note calls out; restored with the exact inverse edit |
| 3.11-3.13 | `test_ingest.py` (cli) | GREEN on first run (no RED — confirms tasks-phase decision 1: the gate was already floored correctly through #1087 + 3.10's fold, once `source_plan.source_sensitivity` reads back the raised value) | N/A | N/A |
| 3.15 | `test_okf_incoming_frontmatter.py` | GREEN on first run (regression pin, confirms 3.5's allow-list does not leak) | N/A | N/A |
| 3.16 (a) tags-only | `test_ingest.py` (cli) | `AssertionError: 'tags added: alpha, beta' in ''` | GREEN after 3.18 | N/A |
| 3.16 (b) both deltas | `test_ingest.py` (cli) | `AssertionError` — `tags added:` line missing | GREEN after 3.18 | N/A |
| 3.16 (c) advisory present | `test_ingest.py` (cli) | `AssertionError: 'openkos set-sensitivity' in <unrelated stderr>` | GREEN after 3.18 | N/A |
| 3.16 (d) advisory absent | `test_ingest.py` (cli) | passed vacuously (predicted — no advisory code existed yet) | stayed GREEN after 3.18 | N/A |
| 3.17 | `test_ingest.py` (cli) | passed vacuously (predicted — no new lines existed yet) | stayed GREEN after 3.18 | N/A |

All `__pycache__` purged before each verdict; every mutation reverted with
the exact inverse edit.

### Deviations from Design

1. **`SourceDocumentPlan` gains three fields design.md's Interfaces/
   Contracts section does not list**: `frontmatter_changed: bool = False`,
   `tags_added: tuple[str, ...] = ()`, `sensitivity_changed: bool = False`.
   Design's contract only lists `lift_changed: bool = False` (the OR of all
   three deltas). Once `lift_changed` became a genuine OR across three
   independent deltas (task 3.18), the CLI's per-delta preview lines could
   no longer reuse it directly the way task 2.15 did in Phase 2 (where
   `lift_changed` WAS exactly the frontmatter delta) — reusing the OR'd
   flag for the frontmatter-only line would have printed "source
   frontmatter recorded" whenever ANY delta fired, including a tags-only or
   sensitivity-only rewrite, which the tests (3.16 case a, 3.17) explicitly
   forbid. Exposing the three specific deltas is the minimal, mechanical
   fix that keeps "the printed line and the skip decision provably in
   agreement" (tasks.md's own stated principle for task 2.15, extended
   here to 3.18). Same shape as the existing `lift_changed`/`tags`/
   `source_frontmatter` fields; no public interface beyond `SourceDocumentPlan`
   itself is affected.
2. **Test names differ from tasks.md's literal names in several places**
   (3.1/3.2/3.4/3.6-3.9), following the SAME splitting pattern Phase 1/2
   already established and documented: a single parametrized test named in
   tasks.md was split into a test class or several focused test functions
   for clarity, with every named scenario still covered. No coverage gap.

### Issues Found

None.

### Review Workload / Size

Actual authored changed lines for this work unit: **744 insertions + 16
deletions across 6 files** (`git diff --shortstat` of commit `6615f4a`,
excluding the second, docs-only commit). This exceeds the review budget
(400) and design.md's own forecast for Slice 3 (~300-450). Phase 3 is
defined as one indivisible PR in `tasks.md`'s Suggested Work Units table
(PR 3 = Phase 3 in full): splitting further would separate a `[TEST]`/
`[IMPL]` pair across PR boundaries (e.g. the tag-union tests from the fold
they pin), or separate the Decision 4 gate-confirmation tests from the
sensitivity-fold implementation they confirm. Per session config,
`size:exception` is invoked for this slice (owner pre-approved this
outcome for unsplittable slices); no attempt was made to shrink the diff by
cutting tests, comments, or docstrings to fit the number.

### Commits

1. `6615f4a` — `feat(ingest): lift incoming tags and sensitivity onto the Source (#1062)`
   (code + all tests; 744 insertions, 16 deletions, 6 files)
2. (this commit) — `docs(sdd): record frontmatter slice 3 progress (#1062)`
   (`tasks.md` checkbox updates + this file)

### Next

All Phase 3 tasks complete. `next_recommended: sdd-archive` (verification
optional per the SDD contract) or `sdd-apply` again for Phase 4 (date lift,
independent of Phase 3, after PR 2 merges) or Phase 5 (derived tag
propagation, after this PR merges).

## Slice 4 (PR 4 → `main`, after PR 2 merges, independent of PR 3): Phase 4 — Source lift: `date:` tier

**Status**: COMPLETE — 16/16 tasks done (4.1-4.16). Strict TDD mode, runner
`uv run pytest`. Branch `feat/1062-frontmatter-p4`, stacked on the
not-yet-merged slice-3 branch (PR #1090) at `ffb2df6`.

**Scope**: the `_tolerant_date` refactor of `read_event_date`'s body;
`okf.read_incoming_date`; `resolve_event_date(incoming=)`, the new
`"frontmatter"` precedence tier and `EventDateOrigin` literal;
`compose_source_document` reading `read_incoming_date` directly off the
already-parsed mapping (tasks-phase decision 2 — independent of
`lift_incoming_frontmatter`); the CLI's frontmatter origin-disclosure line.

### Completed Tasks

- [x] 4.1 `test_read_event_date_unchanged_after_tolerant_date_refactor` —
      PRECONDITION pin, passed vacuously as predicted (written before the
      refactor), stayed GREEN through 4.4
- [x] 4.2 `test_read_incoming_date_shape_table` (5 cases)
- [x] 4.3 `test_read_incoming_date_and_read_event_date_share_tolerance_rules`
      (4 cases) + `test_read_incoming_date_and_read_event_date_agree_on_absent_key`
      (split into two functions — the absent-key row cannot share one
      mapping literal with the other rows, since the two readers use
      different key names)
- [x] 4.4 `_tolerant_date` extracted from `read_event_date`'s body;
      `read_event_date` becomes a thin absent-key wrapper around it;
      `okf.read_incoming_date` added
- [x] 4.5 `test_resolve_event_date_four_tier_precedence_table` (6 cases,
      class name differs from the task's literal function name — same
      splitting/table pattern Phases 1-3 already established)
- [x] 4.6 `test_resolve_event_date_created_key_never_consulted`
- [x] 4.7 `resolve_event_date(incoming=...)`; `EventDateOrigin` gains
      `"frontmatter"`; new precedence branch between `stored` and
      `inferred`
- [x] 4.8 `test_compose_source_document_reads_incoming_date_independently_of_lift`
- [x] 4.9 `compose_source_document` calls `okf.read_incoming_date` on the
      already-parsed `source_frontmatter` mapping, independently of
      `lift_incoming_frontmatter`, and passes it to `resolve_event_date`
- [x] 4.10 `test_event_date_origin_disclosure_line_names_frontmatter`
- [x] 4.11 `_echo_event_date_preview_line` gains the `"frontmatter"` branch:
      `event date {value} (from the source's frontmatter date)`
- [x] 4.12 `ruff check` / `ruff format --check` / `mypy .` — green (one
      `ruff format` pass needed on the new CLI test)
- [x] 4.13 focused command green (52 passed, per tasks.md's literal
      3-file/3-`-k` invocation — pytest applies only the LAST `-k`,
      `event_date`, across all three paths); full `uv run pytest --cov`
      green — 7169 passed, 2 skipped, 96.92% coverage (gate 90%)
- [x] 4.14 confirmed via the EXISTING
      `test_ingest_converged_reingest_with_differing_flag_rewrites_with_no_extraction`
      test (already in the suite from an earlier slice): a Source-only
      rewrite triggered solely by the event-date delta asserts the fake
      LLM's `chat` is NEVER called and derived concept files stay
      byte-identical — re-ran and confirmed GREEN; no new test added, per
      the task's own "confirm (or add if missing)" wording
- [x] 4.15 `evals/run_self_tests.py` — 44/44 green
- [x] 4.16 committed — see Commits

### Files Changed

| File | Action | What |
|---|---|---|
| `src/openkos/model/okf.py` | Modified | `_tolerant_date` (extracted from `read_event_date`'s body); `read_event_date` reduced to an absent-key wrapper around it; `read_incoming_date` |
| `src/openkos/application/ingest.py` | Modified | `EventDateOrigin` gains `"frontmatter"`; `resolve_event_date(incoming=...)` and its new precedence branch; `compose_source_document` computes `incoming_event_date` via `okf.read_incoming_date(source_frontmatter)` and passes it into `resolve_event_date` |
| `src/openkos/cli/main.py` | Modified | `_echo_event_date_preview_line` gains the `"frontmatter"` branch |
| `tests/unit/model/test_okf.py` | Modified | 4.1-4.3 tests |
| `tests/unit/application/test_ingest.py` | Modified | 4.5, 4.6, 4.8 tests |
| `tests/unit/cli/test_ingest.py` | Modified | 4.10 test |
| `openspec/changes/preserve-source-frontmatter/tasks.md` | Modified | 4.1-4.16 marked `[x]` |
| `openspec/changes/preserve-source-frontmatter/apply-progress.md` | Modified | this section |

### TDD Cycle Evidence

| Task(s) | Test file | RED (observed) | GREEN | Mutation |
|---|---|---|---|---|
| 4.1 | `test_okf.py` | passed vacuously (predicted — PRECONDITION pin, nothing changed yet) | stayed GREEN after 4.4 (27/27 `event_date`/`read_incoming_date` tests) | N/A (precondition pin, no mutation task assigned) |
| 4.2 | `test_okf.py` | `AttributeError: module 'openkos.model.okf' has no attribute 'read_incoming_date'` (5 cases) | GREEN after 4.4 | N/A (no mutation task assigned to 4.2 itself) |
| 4.3 | `test_okf.py` | `AttributeError: ... no attribute 'read_incoming_date'` (5 cases: 4 parametrized + 1 absent-key) | GREEN after 4.4 | made `read_incoming_date` accept a `datetime` by dropping its time component (instead of rejecting it): the `datetime` row flipped from `None` to a real `date`, failing the parity assertion (`assert datetime.date(2026, 7, 14) == None`); reverted with the exact inverse edit, `__pycache__` purged before and after |
| 4.5 | `test_ingest.py` (application) | `TypeError: resolve_event_date() got an unexpected keyword argument 'incoming'` (6 cases) | GREEN after 4.7 | N/A (no mutation task assigned) |
| 4.6 | `test_ingest.py` (application) | `TypeError: resolve_event_date() got an unexpected keyword argument 'incoming'` | GREEN after 4.7 | N/A |
| 4.8 | `test_ingest.py` (application) | `AssertionError: assert None == datetime.date(2026, 7, 14)` — `plan.event_date.value` was `None` before the call site existed | GREEN after 4.9 | N/A |
| 4.10 | `test_ingest.py` (cli) | `AssertionError` — the printed line read "kept from the existing Source" instead of the frontmatter wording, confirming the task's predicted failure mode (the `else` branch was catching the new `"frontmatter"` origin as if it were `"kept"`, not raising) | GREEN after 4.11 | N/A (no mutation task assigned) |

All `__pycache__` purged before each verdict; every mutation reverted with
the exact inverse edit.

### Deviations from Design

None — implementation matches design.md Decision 5 and this file's
tasks-phase decision 2 (`compose_source_document` calls `okf.
read_incoming_date` directly on the already-parsed mapping, never through
`lift_incoming_frontmatter`, keeping Phases 3 and 4 mergeable in either
order). Test names differ from tasks.md's literal names in the usual
places (4.1, 4.3's split, 4.5), following the same splitting pattern
Phases 1-3 already established and documented — every named scenario is
still covered.

### Issues Found

None.

### Review Workload / Size

Actual authored changed lines for this work unit: **321 insertions + 28
deletions across 6 files** (`git diff --shortstat` of commit `3ad9682`,
excluding the second, docs-only commit) — within the review budget (400)
and design.md's own forecast for Slice 4 (~250-400). No `size:exception`
needed for this slice.

### Commits

1. `3ad9682` — `feat(ingest): resolve event_date from incoming frontmatter's date tier (#1062)`
   (code + all tests; 321 insertions, 28 deletions, 6 files)
2. (this commit) — `docs(sdd): record frontmatter slice 4 progress (#1062)`
   (`tasks.md` checkbox updates + this file)

### Next

All Phase 4 tasks complete. `next_recommended: sdd-archive` (verification
optional per the SDD contract) or `sdd-apply` again for Phase 5 (derived
tag propagation, after PR 3 merges) or Phase 6 (docs, after PR 2 merges,
parallel-eligible with Phases 3-5).

## Slice 5 (PR 5 → `main`, after PR 3 merges): Phase 5 — derived tag propagation

**Status**: COMPLETE — 10/10 tasks done (5.1-5.10). Strict TDD mode, runner
`uv run pytest`. Branch `feat/1062-frontmatter-p5`, stacked on the
not-yet-merged slice-4 branch (PR #1091, not yet merged; slice 3 with the
tag lift is already on `main`).

**Scope**: `okf.build_concept(tags=)` (byte-identical default),
`stage_derived_objects(source_tags=)`, the CLI threading
`source_plan.tags` into the `stage_derived_objects` call, and a merge-union
regression pin (design.md Decision 6).

### Completed Tasks

- [x] 5.1 `test_build_concept_default_tags_byte_identical` — PRECONDITION
      golden reused verbatim from
      `test_build_concept_output_byte_identical_regression`; confirmed
      GREEN before 5.3 (nothing to diverge from yet, no `tags` parameter
      existed) and confirmed it STAYED GREEN after 5.3 landed
- [x] 5.2 `test_build_concept_emits_given_tags`
- [x] 5.3 `okf.build_concept` gains `tags: Sequence[str] = ()`; the
      hard-coded `"tags": []` metadata literal becomes
      `"tags": list(tags)`; both existing production callers
      (`application/ingest.py`'s pre-Phase-5 call, `application/query.py`'s
      `stage_filed_answer`) confirmed to pass no `tags=` argument and so
      keep emitting `[]` unchanged (`query --save`'s output is untouched by
      this change)
- [x] 5.4 `test_stage_derived_objects_threads_source_tags_to_every_build_concept_call`
      — two DISTINCT candidates (`_fake_extractor([first, second])`, so
      both stage), asserting `tags: [alpha, beta]` on BOTH staged plans'
      rendered content
- [x] 5.5 `test_stage_derived_objects_carried_path_ignores_source_tags` —
      PRECONDITION: `carried` genuinely set (a real `ConvergedReingest`);
      the pre-extraction short-circuit returns before `source_tags` is
      ever consulted
- [x] 5.6 `stage_derived_objects` gains `source_tags: tuple[str, ...] = ()`;
      `tags=source_tags` threaded into the `okf.build_concept` call inside
      the staging loop, beside the existing `sensitivity=resolved_sensitivity`
      argument
- [x] 5.7 `test_derived_object_created_in_run_inherits_sources_tags` — a
      fresh ingest with `tags: [alpha, beta]` incoming frontmatter; the
      written derived object's `tags` include the Source's resolved
      (unioned) tags
- [x] 5.8 `test_existing_derived_object_unaffected_by_later_source_tag_change`
      — PRECONDITION: the existing derived object's bytes captured before
      the re-ingest (and its pre-run `tags == []` confirmed); a re-ingest
      that lifts a NEW tag onto the Source leaves the existing derived
      object's file byte-unchanged (create-only reconciliation);
      passed vacuously before 5.9 (create-only reconciliation already
      applies to every field), confirmed as a regression pin
- [x] 5.9 `cli/main.py`'s `stage_derived_objects` call gains
      `source_tags=source_plan.tags`, beside
      `stamp_sensitivity=source_plan.source_sensitivity`
- [x] 5.10 `test_build_merged_document_tags_generic_union_unaffected` — a
      survivor `tags: [alpha]` and an absorbed `tags: [beta]` merge to
      `tags: [alpha, beta]`; passed vacuously (the existing generic
      list-union already covers `tags`, which is not in `_SPECIAL_KEYS`) —
      pure regression pin, no `[IMPL]` task paired, per design.md Decision 6
- [x] 5.11 `ruff check` / `ruff format --check` / `mypy .` — green (one
      `ruff format` pass needed on the new CLI tests)
- [x] 5.12 focused command
      (`tests/unit/model/test_okf.py tests/unit/application/test_ingest.py
      tests/unit/cli/test_ingest.py`) green — 712 passed; full
      `uv run pytest --cov` green — 7176 passed, 2 skipped, 96.92% coverage
      (gate 90%)
- [x] 5.13 `evals/run_self_tests.py` — 44/44 green
- [x] 5.14 committed — see Commits

### Files Changed

| File | Action | What |
|---|---|---|
| `src/openkos/model/okf.py` | Modified | `build_concept` gains `tags: Sequence[str] = ()`; `"tags": []` literal becomes `"tags": list(tags)`; docstring updated |
| `src/openkos/application/ingest.py` | Modified | `stage_derived_objects` gains `source_tags: tuple[str, ...] = ()`; `tags=source_tags` threaded into the staging loop's `okf.build_concept` call; docstring note |
| `src/openkos/cli/main.py` | Modified | `stage_derived_objects` call site gains `source_tags=source_plan.tags` |
| `tests/unit/model/test_okf.py` | Modified | 5.1, 5.2, 5.10 tests |
| `tests/unit/application/test_ingest.py` | Modified | 5.4, 5.5 tests |
| `tests/unit/cli/test_ingest.py` | Modified | 5.7, 5.8 tests |
| `openspec/changes/preserve-source-frontmatter/tasks.md` | Modified | 5.1-5.10 marked `[x]` |
| `openspec/changes/preserve-source-frontmatter/apply-progress.md` | Modified | this section |

### TDD Cycle Evidence

| Task(s) | Test file | RED (observed) | GREEN | Mutation |
|---|---|---|---|---|
| 5.1 | `test_okf.py` | passed vacuously (predicted — no `tags` parameter exists yet, nothing to diverge from) | stayed GREEN after 5.3 | N/A (no mutation task assigned) |
| 5.2 | `test_okf.py` | `TypeError: build_concept() got an unexpected keyword argument 'tags'` | GREEN after 5.3 | changed `"tags": list(tags)` back to `"tags": []`; `test_build_concept_emits_given_tags` flipped to `AssertionError: assert [] == ['alpha', 'beta']`; reverted with the exact inverse edit, `__pycache__` purged before and after |
| 5.4 | `test_ingest.py` (application) | `TypeError: stage_derived_objects() got an unexpected keyword argument 'source_tags'` | GREEN after 5.6 | N/A (no mutation task assigned) |
| 5.5 | `test_ingest.py` (application) | `TypeError: stage_derived_objects() got an unexpected keyword argument 'source_tags'` | GREEN after 5.6 | N/A (no mutation task assigned; regression pin) |
| 5.7 | `test_ingest.py` (cli) | `AssertionError: assert [] == ['alpha', 'beta']` — the derived object's `tags` were empty before the CLI threaded `source_plan.tags` through | GREEN after 5.9 | N/A (no mutation task assigned) |
| 5.8 | `test_ingest.py` (cli) | passed vacuously (predicted — create-only reconciliation already protects every field) | stayed GREEN after 5.9 | N/A (no mutation task assigned; regression pin) |
| 5.10 | `test_okf.py` | passed vacuously (predicted — the generic list union already covers `tags`) | N/A — no `[IMPL]` task pairs with this one | N/A (no mutation task assigned; regression pin) |

All `__pycache__` purged before each verdict; every mutation reverted with
the exact inverse edit.

### Deviations from Design

None — implementation matches design.md Decision 6 exactly: `build_concept`
gains a `tags` parameter defaulting to byte-identical `[]` output;
`stage_derived_objects` threads the Source's resolved (union) tags, never
just the freshly lifted subset, into every staged `build_concept` call;
the `carried=` short-circuit and pre-extraction returns never see
`source_tags`, since they create no derived object; the merge-time union
needed no code change, only a regression pin, since `tags` was never added
to `build_merged_document`'s `_SPECIAL_KEYS`.

### Issues Found

None.

### Review Workload / Size

Actual authored changed lines for this work unit: **221 insertions + 2
deletions across 6 files** (`git diff --shortstat` of commit `df660c6`,
excluding the second, docs-only commit) — well within the review budget
(400) and design.md's own forecast for Slice 5 (unspecified in the Review
Workload Forecast table's per-slice column, but well under the general
~1,450-2,300 total across 6 slices). No `size:exception` needed for this
slice.

### Commits

1. `df660c6` — `feat(ingest): propagate a Source's resolved tags onto derived objects created in the same run (#1062)`
   (code + all tests; 221 insertions, 2 deletions, 6 files)
2. (this commit) — `docs(sdd): record frontmatter slice 5 progress (#1062)`
   (`tasks.md` checkbox updates + this file)

### Next

All Phase 5 tasks complete. `next_recommended: sdd-archive` (verification
optional per the SDD contract) or `sdd-apply` again for Phase 6 (docs,
after PR 2 merges, parallel-eligible with Phases 3-5 — Phase 3 and Phase 5
are both already on `main`/merged-pending, so Phase 6 has no remaining
blocking dependency once PR 2 has merged, which it already has).
