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
