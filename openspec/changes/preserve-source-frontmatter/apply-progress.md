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
