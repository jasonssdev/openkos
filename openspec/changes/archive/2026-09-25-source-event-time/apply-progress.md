# Apply Progress: source-event-time

Change: `source-event-time`. Project: openkos. Store: hybrid
(`tasks.md` checkboxes + this file; Engram mirroring is handled by the
orchestrator, not this run).

## Scope of this run

Three runs so far:

- Run 1: Slice 1 only (`## Slice 1 (PR 1 → main): OKF seam and parser`).
- Run 2: Slice 2 only (`## Slice 2 (PR 2 → PR 1's branch): Application
  resolution`), per the orchestrator's PR boundary. Branch
  `feat/1014-source-event-time-2`, stacked on `feat/1014-source-event-time`
  (slice 1, PR #1016).
- Run 3 (this update): Slice 3 only (`## Slice 3 (PR 3 → PR 2's branch):
  CLI and docs`), per the orchestrator's PR boundary. Branch
  `feat/1014-source-event-time-3`, stacked on `feat/1014-source-event-time-2`
  (slice 2, PR #1017). All 53 tasks across all three slices are now `[x]`
  in `tasks.md`.

## Mode

Strict TDD. Test runner: `uv run pytest`.

## Slice 1 TDD Cycle Evidence

| Task | RED (observed) | GREEN | REFACTOR |
|---|---|---|---|
| 1.1-1.3 (`test_source_date.py`) | `ImportError: cannot import name 'source_date' from 'openkos'` (collection-time; matches the predicted `ModuleNotFoundError` in substance) | `uv run pytest tests/unit/test_source_date.py` — 26/26 passed | None needed; module written once, matched the design table exactly |
| 1.4 (`source_date.py`) | (paired with 1.1-1.3 above) | as above | — |
| 1.5-1.8, 1.10 (`test_okf.py` key/emission/reader) | `AttributeError: module 'openkos.model.okf' has no attribute 'StoredEventDate'` at collection time (blocks the whole file — a parametrize decorator references `okf.StoredEventDate` before collection) | `uv run pytest tests/unit/model/test_okf.py -k event_date` — 15/15 passed on first implementation attempt (merge tests not yet included) | Mutation falsification below |
| 1.9, 1.11 (`okf.py` key/emission/reader) | (paired above) | as above | — |
| 1.12 (merge exclusion, absorbed side) | Genuine `AssertionError`: `'event_date' not in merged` failed — today's generic "adopt when absent" branch copied the absorbed value, exactly as design.md predicted | After adding `EVENT_DATE_KEY` to `_SPECIAL_KEYS`: passed | — |
| 1.13 (merge exclusion, survivor's own value) | Passed on first run without the `_SPECIAL_KEYS` change (accidental green under the pre-existing generic scalar rule, exactly as tasks.md 1.13 predicted) — kept as a regression pin, not treated as a RED-then-GREEN task | Still passes after 1.14 | — |
| 1.14 (`_SPECIAL_KEYS` + docstrings) | (paired with 1.12) | `uv run pytest tests/unit/model/test_okf.py -k event_date` — 16/16 passed | — |

**Mutation falsification** (project convention: a test passing on first
implementation is checked by mutating the exact line it guards):
reordered `read_event_date`'s `isinstance(raw, datetime)` /
`isinstance(raw, date)` checks (date-check first). Re-ran
`test_read_event_date` — `metadata3` (the dedicated `datetime` case)
failed as expected (`AssertionError`, `value` and `malformed` differed).
Reverted byte-exactly (inverse edit, not `git checkout --`), purged
`__pycache__` (`find . -name __pycache__ -prune -exec rm -rf {} +`),
re-ran — 10/10 passed again.

## Slice 1 Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and result | `uv run pytest tests/unit/test_source_date.py tests/unit/model/test_okf.py -q` — 265 passed |
| Runtime harness | N/A — pure functions and a builder/reader with no CLI or workspace surface; `event_date` is not wired to any caller yet (confirmed by the full suite passing with zero edits to `cli/main.py` or `application/ingest.py`) |
| Rollback boundary | Revert `src/openkos/source_date.py`, the `src/openkos/model/okf.py` diff, `tests/unit/test_source_date.py`, and the `tests/unit/model/test_okf.py` diff (docs/adr/0023 and its README row can stay or go independently — nothing in code depends on them). No caller references `event_date` yet, so reverting removes the feature cleanly. |

## Slice 1 verification (tasks 1.15-1.16)

- `uv run ruff check .` — All checks passed! (after fixing 3 `DTZ001` on
  naive `datetime()` test fixtures — added `tzinfo=UTC` — and 2 `RUF001`
  on the deliberately non-ASCII Arabic-Indic digit test case — added
  `# noqa: RUF001`)
- `uv run ruff format --check .` — reformatted `tests/unit/model/test_okf.py`
  once (a wrapped `-> None:` signature), then clean
- `uv run mypy .` — Success: no issues found in 313 source files
- `uv run pytest` (full suite, unpiped) — **6407 passed, 2 skipped** in
  331.49s. Includes every characterization file named in design.md's
  Testing Strategy table (`test_ingest_characterization.py`,
  `test_frontmatter_split_parity.py`, `test_okf_framing_characterization.py`,
  `test_adr_index.py`, every existing `test_build_source_concept_*`
  golden) — all unedited, all green.

## Slice 1 commits (task 1.17)

Two work-unit commits on `feat/1014-source-event-time` (targeting
`main` per PR 1's boundary), split along the natural sub-units (parser
leaf, then the OKF seam) rather than as one combined commit:

1. `f3af172` — `feat(model): add source_date parser leaf for event dates`
   (`src/openkos/source_date.py`, `tests/unit/test_source_date.py`; 172
   lines)
2. `3b465f2` — `feat(model): add event_date frontmatter key, reader, and
   merge exclusion` (`src/openkos/model/okf.py`,
   `tests/unit/model/test_okf.py`, `docs/adr/0023-source-event-date.md`,
   `docs/adr/README.md`; 305 lines)

Staged only source, test files, and the ADR docs, per the orchestrator's
scope. `openspec/changes/source-event-time/` was deliberately left
untracked (lands with the archive PR, per repo precedent). No `git add -A`
or `git add .` used. Nothing pushed.

## Slice 1 Review Workload / Budget

`git diff --stat main...HEAD` across both commits: **6 files changed,
477 insertions(+), 4 deletions(-) = 481 authored changed lines**, over
the ~400-line advisory budget and above tasks.md's own ~235-line forecast
for this slice. The gap is comment/docstring density: this repo's
convention (see `source_title.py`, `okf.py`'s existing key docstrings)
documents every new public seam (`EVENT_DATE_KEY`, `StoredEventDate`,
`read_event_date`, the module docstring of `source_date.py`) at the same
density as the surrounding code it sits beside, which the constraints for
this run explicitly required matching. No code, comments, blank lines, or
tests were trimmed to chase the budget (explicitly disallowed). This
slice is already the smallest cohesive unit in design.md's 3-slice plan
— `source_date.py` and the `okf.py` seam are the two natural sub-units,
already split into separate commits above; splitting further (e.g. key
vs. reader vs. merge) would break mid-abstraction and leave the seam
inconsistently documented across two commits reviewed separately. This is
reported honestly as a `size:exception` candidate for the orchestrator's
delivery-strategy tracking; no further splitting was attempted.

## Slice 1 deviations from design

None. Implementation matches design.md's Decision 1, 2, and 8 exactly:
ASCII `[0-9]` in the flag/token regexes with Unicode `\d` boundary
guards, the `datetime`-before-`date` reader ordering, the quoted-string
emission with alphabetical sort between `description` and `freshness`,
and `event_date`'s exclusion from the merge's generic fill-the-gap
branch.

---

## Slice 2 TDD Cycle Evidence

Test runner: `uv run pytest`. Test file: `tests/unit/application/test_ingest.py`.
Safety net before any edit: `uv run pytest tests/unit/application/test_ingest.py -q`
— 41 passed (baseline, zero pre-existing failures).

| Task | RED (observed) | GREEN | REFACTOR |
|---|---|---|---|
| 2.1 (`test_resolve_event_date_precedence`) | `AttributeError: module 'openkos.application.ingest' has no attribute 'resolve_event_date'` (5/5 parametrized cases failed identically) — matches the predicted `ImportError` in substance | `uv run pytest ... -k test_resolve_event_date_precedence` — 5/5 passed | None needed; matched design.md Decision 4's table exactly on first implementation |
| 2.2 (`resolve_event_date`, `EventDateResolution`) | (paired with 2.1 above) | as above | — |
| 2.3 (3 `compose_source_document` tests) | `TypeError: compose_source_document() got an unexpected keyword argument 'event_date_flag'` and `AttributeError: 'SourceDocumentPlan' object has no attribute 'event_date'` — both predicted | `uv run pytest tests/unit/application/test_ingest.py -q` — 49/49 passed | — |
| 2.4 (`_read_source_event_date`, `compose_source_document` threading, `SourceDocumentPlan.event_date`) | (paired with 2.3 above) | as above | — |
| 2.5 (`test_compose_catalog_update_preserves_event_date_on_marker_only_rebuild`) | Genuine `AssertionError: None == '2026-07-14'` — the second `build_source_concept` call site omitted `event_date=`, exactly as design.md predicted | `uv run pytest tests/unit/application/test_ingest.py -q` — 50/50 passed | — |
| 2.6 (`compose_catalog_update` `event_date=` pass-through) | (paired with 2.5 above) | as above | — |
| 2.7 (`test_stage_derived_objects_returns_carried_markers_without_llm_call`) | `TypeError: stage_derived_objects() got an unexpected keyword argument 'carried'` | `uv run pytest tests/unit/application/test_ingest.py -q` — 52/52 passed | — |
| 2.8 (`test_carried_extraction_status_fails_closed`) | `AttributeError: module 'openkos.application.ingest' has no attribute 'carried_extraction_status'` | as above | — |
| 2.9 (`carried_extraction_status`, `ConvergedReingest.carried_status`, `stage_derived_objects(carried=...)`) | (paired with 2.7/2.8 above) | as above | mypy flagged `Redundant cast to "Literal[...]"` on `carried_extraction_status`'s `cast(okf.ExtractionStatus, raw)` (mypy narrows via the `in known` membership check already); removed the now-unneeded `cast` and its `typing` import |

**Mutation falsification, both directions** (task 2.10 — project convention:
every guard is checked by mutating the exact line it guards):

- **Narrow-direction**: changed `if carried is not None:` to
  `if False and carried is not None:` in `stage_derived_objects`. Purged
  `__pycache__`, re-ran `test_stage_derived_objects_returns_carried_markers_without_llm_call`
  — failed as expected: the stub `_FakeLLM(raises=AssertionError("must not
  be called"))`'s `chat` was actually invoked (via `extract_concept` →
  `_extract_once`), raising `AssertionError: must not be called` — proves
  the short-circuit is load-bearing, not a no-op.
- **Broad-direction**: changed the same guard to `if True:` (short-circuit
  fires unconditionally, ignoring `carried is None`). Purged `__pycache__`,
  re-ran `test_stage_derived_objects_returns_plans_on_success` (an ordinary
  extraction test with `carried` omitted) — failed as expected:
  `AttributeError: 'NoneType' object has no attribute 'carried_status'`
  — proves a normal ingest would stop extracting entirely without the
  `is not None` check.
- Reverted both mutations byte-exactly (inverse edit, not `git checkout
  --`), purged `__pycache__` (`find . -name __pycache__ -prune -exec rm
  -rf {} +`), re-ran the full file — 52/52 passed again.

### Test Summary
- **Total tests written**: 11 (`test_resolve_event_date_precedence` counts
  as 5 parametrized cases, `test_carried_extraction_status_fails_closed`
  as 1 function covering the whole vocabulary plus 4 fail-closed branches)
- **Total tests passing**: 52/52 in `tests/unit/application/test_ingest.py`
  (41 baseline + 11 new)
- **Layers used**: Unit (11) — pure-function tables and one stub-`LLMBackend`
  call, no CLI/workspace surface (matches the tasks.md Work Unit table)
- **Approval tests** (refactoring): None — no refactoring tasks this slice
- **Pure functions created**: `resolve_event_date`, `carried_extraction_status`
  (plus `EventDateResolution.changed`, a pure property)

## Slice 2 Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and result | `uv run pytest tests/unit/application/test_ingest.py -q` — 52 passed |
| Runtime harness | N/A — `application/ingest.py` functions called directly in unit tests with a stub `LLMBackend`; `cli/main.py` does not yet pass `event_date_flag`/`source_name` (confirmed by the full suite passing with zero edits to `cli/main.py`), so no end-to-end `ingest` scenario exists yet, exactly as tasks.md's Suggested Work Units table states |
| Rollback boundary | Revert the `src/openkos/application/ingest.py` diff and the `tests/unit/application/test_ingest.py` diff (one commit, `f8f406a`). `cli/main.py` still calls the pre-slice-2 signatures with defaults (`event_date_flag=None, source_name=None`), so `ingest` behavior is completely unchanged by reverting this slice alone. |

## Slice 2 verification (tasks 2.11-2.12)

- `uv run ruff check .` — All checks passed!
- `uv run ruff format --check .` — reformatted `tests/unit/application/test_ingest.py`
  once (line-wrapping in the new parametrized test and long assert
  expressions), then clean
- `uv run mypy .` — Success: no issues found in 313 source files (after
  removing the redundant `cast`, see TDD Cycle Evidence 2.9 above)
- `uv run pytest` (full suite, unpiped) — **6418 passed, 2 skipped** in
  318.25s. Delta from slice 1's baseline (6407 passed, 2 skipped) is
  exactly +11, matching the 11 new tests. Includes every characterization
  file named in design.md's Testing Strategy table, all unedited, all
  green.

## Slice 2 commits (task 2.13)

One work-unit commit on `feat/1014-source-event-time-2` (stacked on
`feat/1014-source-event-time`, targeting PR 1's branch per PR 2's
boundary). Kept as one commit rather than split further: tasks.md's own
"Suggested Work Units" table groups ALL of 2.1-2.10 under one row (one
shared focused-test-command, runtime-harness, and rollback-boundary), and
the two sub-groups (`resolve_event_date`/threading, and the
carried-marker short-circuit) are tightly coupled in practice — Decision
6's short-circuit is what makes the date-only rewrite Decision 4/3
resolve actually useful once slice 3 wires in the CLI; splitting them
would leave one commit's feature inert without the other landing first,
which is a worse reviewer experience than the size overage below.

1. `f8f406a` — `feat(ingest): resolve event_date precedence and carry it
   through catalog rebuilds` (`src/openkos/application/ingest.py`,
   `tests/unit/application/test_ingest.py`; 431 lines)

Staged only `src/openkos/application/ingest.py` and
`tests/unit/application/test_ingest.py`, by explicit path — no `git add
-A` or `git add .`. `openspec/changes/source-event-time/` remains
untracked (per slice 1's orchestrator note below; unchanged this run).
Nothing pushed.

## Slice 2 Review Workload / Budget

`git diff --stat feat/1014-source-event-time...HEAD`: **2 files changed,
431 insertions(+), 3 deletions(-) = 431 authored changed lines**, over
tasks.md's own ~215-line forecast for this slice and over the ~400-line
advisory budget — the same comment/docstring-density pattern slice 1
reported (every new public function/dataclass field is documented at the
density the surrounding module already uses, e.g. `EventDateResolution`'s
five fields, `resolve_event_date`'s precedence rationale, the `carried`
parameter's short-circuit rationale on `stage_derived_objects`). No code,
comments, blank lines, or tests were trimmed to chase the budget
(explicitly disallowed by the run's constraints). As explained under
"Slice 2 commits" above, this slice's two natural sub-groups are load-bearing
on each other, so splitting further was rejected rather than attempted a
second time. Reported honestly as a `size:exception` candidate for the
orchestrator's delivery-strategy tracking.

## Slice 2 deviations from design

None. Implementation matches design.md Decisions 3, 4, and 6 exactly:
the flag > stored > file name > unset precedence order, `previous` and
`stored_malformed` derived from a non-malformed stored value only, the
second `build_source_concept` call site inside `compose_catalog_update`
now receiving `event_date=source.event_date.value`, and the
carried-marker short-circuit returning before any LLM call. One
implementation note not spelled out verbatim in design.md: `carried_
extraction_status` needed `isinstance(raw, str) and raw in known` (the
same shape `okf.extraction_notices` already uses) rather than a bare
membership test, so a non-`str` on-disk value (e.g. `extraction_status:
true`) fails closed instead of raising — this is consistent with design.md's
"mirroring `carried_extraction_notice`'s fail-closed membership check"
instruction, just spelled out at the type level.

---

## Slice 3 TDD Cycle Evidence

Test runner: `uv run pytest`. Test file: `tests/unit/cli/test_ingest.py`.
No separate safety net run was needed beyond the full-suite baseline
already proven clean in slice 2 (6418 passed, 2 skipped) -- every slice-3
test is new, none modifies an existing test.

15 tests covering tasks 3.1-3.18's paired `[TEST]`/`[IMPL]` groups, plus
one extra batch-cost-gate regression test (below), were written first and
observed RED, then made GREEN by one implementation pass over
`cli/main.py` covering all of 3.3, 3.5, 3.8, 3.11, 3.16 at once (the tasks
are tightly sequential edits to the same function, `_ingest_single`, so
splitting the implementation pass across them would have meant repeatedly
re-running a half-finished command body):

| Tasks | RED (observed) | GREEN |
|---|---|---|
| 3.1-3.3 (flag validation + refusal wording) | `exit_code == 2` from Click's own unrecognized-`--event-date`-option usage error, not the designed refusal wording -- both `test_ingest_rejects_invalid_event_date_flag_before_any_write` cases and the directory/glob tests failed on the wording/exit-code assertions | `uv run pytest -k "rejects_invalid_event_date or rejects_event_date_with_a"` -- 3/3 passed |
| 3.4-3.8, 3.12 (threading, preview line, flag/file-name/kept/replacing origins) | `AssertionError` -- no `event date ...` line existed on stdout at all (the option was recognized after 3.3, but never reached `compose_source_document` or the preview) | `uv run pytest -k "writes_event_date_from or reingest_keeps_event_date or reingest_overwrites_event_date or prints_no_event_date_line"` -- 5/5 passed |
| 3.9-3.11 (malformed-value warning) | `AssertionError` -- no warning on stderr; the regenerated document still carried the hand-edited malformed value (nothing read `stored_malformed`) | `uv run pytest -k "warns_on_malformed or fills_malformed"` -- 2/2 passed |
| 3.13-3.16 (convergence condition, carried-marker rewrite) | `AssertionError` on the unwritten/unchanged frontmatter -- the pre-existing `#773` gate returned early on every converged Source regardless of `event_date`, so nothing was ever rewritten and the stub LLM was correctly never called (matching tasks.md's predicted RED reason exactly) | `uv run pytest -k "converged_reingest_with_differing_flag or converged_reingest_backfills"` -- 2/2 passed |
| 3.17 (idempotent date-only rewrite) | Regression pin, written and immediately GREEN alongside 3.16 per tasks.md's own instruction (not a RED-first task) | `uv run pytest -k converged_date_only_rewrite_is_idempotent` -- 1/1 passed |
| 3.18 (derived objects never carry `event_date`) | Regression pin, trivially GREEN today (no derived-object code path receives `event_date`) as tasks.md predicted | `uv run pytest -k never_writes_event_date_on_a_derived_concept` -- 1/1 passed |
| extra: batch cost-gate accuracy under Decision 6 | Not a tasks.md item -- added because `_reingest_will_skip` (the batch cost-gate predictor named in this run's mutation-falsification scope) has NO task coverage in tasks.md or design.md; analysis showed its "0 calls" prediction stays correct on BOTH branches of the narrowed #773 gate (a clean skip and a date-only rewrite both spend zero LLM calls), so this test PINS that correctness rather than driving a RED-then-GREEN change | `uv run pytest -k batch_cost_gate_bills_zero_for_a_converged_date_only_rewrite` -- passed on first run (no implementation change needed; see mutation (c) below for the falsification proving it is load-bearing, not vacuous) |

**Post-GREEN regression** (task 2.11-style gate not repeated for slice 3
tests, since the RED-observation table above already IS that evidence):
the help-text traceability gate `test_no_command_help_publishes_internal_
references` caught `issue #1014c`/`ADR-` leaking into the published
`--event-date` `typer.Option(help=...)` text (a `## Rules` project
convention: `help=` must stay clean, traceability stays in the source
comment beside it). Fixed by moving the ticket/ADR reference into a code
comment above the `typer.Option(...)` call and rewriting `help=` to plain
prose; re-ran the full suite clean afterward.

**Mutation falsifications** (four required by this run's scope, project
convention: every guard is checked by mutating the exact line it guards,
purging `__pycache__` before each re-run, reverting byte-exactly
afterward -- never `git checkout --`):

1. **Directory/glob refusal check** (`cli/main.py`, the up-front
   `--event-date` shape check): mutated `if not src.is_file() and (...)`
   to `if False and not src.is_file() and (...)`. Re-ran
   `test_ingest_rejects_event_date_with_a_directory_before_any_write` and
   `..._with_a_glob_...` -- both failed as expected (`exit_code == 1`
   instead of `2`, the ordinary "does not exist"/expansion path taking
   over). Reverted; both pass again.
2. **Convergence gate** (`cli/main.py`, `if converged is not None and not
   source_plan.event_date.changed:`): two directions.
   - **Narrow-direction** (never take the date-only-rewrite path): mutated
     the condition to `if converged is not None:` (drop the `changed`
     check, restoring the pre-#1014c unconditional skip). Re-ran
     `test_ingest_converged_reingest_with_differing_flag_...` and
     `..._backfills_event_date_from_file_name` -- both failed as expected
     (`AssertionError`: the stored/frontmatter value stayed at its OLD
     value / stayed absent, since the gate skipped and wrote nothing).
     `test_ingest_converged_date_only_rewrite_is_idempotent` stayed GREEN
     (expected -- an unconditional skip is trivially idempotent too, so
     this mutation direction cannot falsify that pin; see mutation
     (2b) below for the direction that does).
   - **Broad-direction** (never skip -- always fall through to a full
     run): mutated the condition to `if False:`. Re-ran the pre-existing
     `#773` regression test `test_reingest_of_extracted_source_skips_
     extraction` AND this slice's own `test_ingest_converged_reingest_
     with_equal_resolved_date_writes_nothing` -- both failed as expected
     (the stub LLM's `AssertionError("must not be called")` fired via
     `embed`, proving a full run -- including the embed step -- executed
     where nothing should have). This direction is what proves (2a)'s
     idempotence pin is not vacuous: it demonstrates a real code path this
     slice's convergence condition must exclude.
   - Reverted byte-exactly; both directions' targeted tests pass again,
     confirmed together with the full slice-3 suite (17/17).
3. **Batch skip predictor** (`cli/main.py`, `_reingest_will_skip`'s final
   `return not application_ingest.extraction_retry_due(metadata)`):
   `_reingest_will_skip` is not named in tasks.md or design.md at all (it
   is an orchestrator-scope-only mutation target), and analysis showed it
   needs NO code change -- Decision 6 guarantees zero LLM calls on both
   the clean-skip and the date-only-rewrite branches, so its "0 calls"
   prediction stays accurate either way. To prove that claim is not
   unfalsifiable, mutated the return to
   `return False and not application_ingest.extraction_retry_due(metadata)`
   (predictor never predicts a skip). Re-ran the new
   `test_batch_cost_gate_bills_zero_for_a_converged_date_only_rewrite` --
   failed as expected (`~3 LLM call(s)` billed instead of `~0`, and the
   run then actually called the stub's `chat`, raising
   `AssertionError("must not be called")`, since the cost estimate no
   longer matched the real skip). Reverted; passes again. This confirms
   `_reingest_will_skip` is correctly UNCHANGED for this slice, not
   silently untested.
4. **Preview line's origin label** (`cli/main.py`,
   `_echo_event_date_preview_line`'s `elif resolution.origin == "file
   name":`): mutated to `elif resolution.origin == "kept":` (swapping
   which origin token reaches the "from the file name" wording). Re-ran
   `test_ingest_writes_event_date_from_file_name_with_no_flag` and
   `test_ingest_reingest_keeps_event_date_with_no_flag` -- both failed as
   expected (`AssertionError`: neither "from the file name" nor "kept from
   the existing Source" appeared in stdout, since "file name" origin fell
   through to the `else` branch's "kept" wording, and "kept" origin no
   longer matched any branch). Reverted; both pass again.

### Test Summary
- **Total tests written**: 16 (15 mapped to tasks.md tasks + 1 extra
  batch-cost-gate regression pin, see table above)
- **Total tests passing**: `tests/unit/cli/test_ingest.py` full file --
  347 passed (331 pre-existing + 16 new), confirmed together with
  `test_ingest_characterization.py` (unedited, all still passing)
- **Layers used**: CLI/integration (16) -- every test drives the real
  `ingest` command through `CliRunner`, exactly as tasks.md's Suggested
  Work Units table specifies for this slice (the first slice where
  `event_date` is reachable end-to-end at all)
- **Approval tests** (refactoring): None -- no refactoring tasks this
  slice
- **Pure functions created**: None new this slice (`_echo_event_date_
  preview_line` is a presentation helper, not pure -- it calls
  `typer.echo`, matching this file's existing `_echo_type_alternative_
  summary`/`_echo_type_floor_summary` convention)

## Slice 3 Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and result | `uv run pytest tests/unit/cli/test_ingest.py tests/unit/cli/test_ingest_characterization.py -q` -- 347 passed |
| Runtime harness | `CliRunner`-driven `openkos ingest` invocations against a real `openkos init` workspace under `tmp_path` (via `_init_workspace`) -- this IS the first real end-to-end exercise of the feature, exactly as tasks.md's Suggested Work Units table names it. A separate live-subprocess smoke against an unreachable `OLLAMA_HOST` was deliberately NOT run: it risks a real network attempt before failing, which the orchestrator's own constraint flags as unacceptable; the `CliRunner` harness is the instructed preferred alternative and already exercises every scenario (fresh ingest with a flag, file-name inference, kept/replacing re-ingest, the malformed-value warning, and the converged date-only rewrite with an LLM stub that raises on any `chat()` call) |
| Rollback boundary | Revert the `src/openkos/cli/main.py` diff (commit `a6f1404`), the `tests/unit/cli/test_ingest.py` diff (same commit), and `docs/cli.md`'s one added row (commit `5683a31`). `_ingest_single`'s `event_date` parameter defaults to `None` and `_ingest_batch` never passes it, so reverting this slice alone restores `ingest`'s pre-`--event-date` behavior exactly -- slices 1 and 2 (already merged/stacked) are unaffected and stay independently functional |

## Slice 3 verification (tasks 3.20-3.22)

- `uv run ruff check .` -- All checks passed!
- `uv run ruff format --check .` -- reformatted `src/openkos/cli/main.py`
  once (a wrapped `if` condition's continuation line), then clean
- `uv run mypy .` -- Success: no issues found in 313 source files
- `uv run pytest --cov` (full suite, unpiped) -- **6434 passed, 2
  skipped** in 377.35s; coverage 97.12% (gate: 90%). Delta from slice 2's
  baseline (6418 passed, 2 skipped) is exactly +16, matching the 16 new
  tests. Includes every characterization file named in design.md's
  Testing Strategy table, all unedited, all green.
- Runtime harness (task 3.22): satisfied via the `CliRunner` end-to-end
  tests described above rather than a live subprocess smoke, per the
  orchestrator's own constraint against a real-network-risking manual
  smoke; see "Slice 3 Work Unit Evidence" above for the full scenario
  list covered.

## Slice 3 commits (task 3.23)

Two work-unit commits on `feat/1014-source-event-time-3` (stacked on
`feat/1014-source-event-time-2`, targeting PR 2's branch per PR 3's
boundary):

1. `a6f1404` -- `feat(cli): add --event-date to ingest with refusals,
   carry-forward, and the converged date-only rewrite`
   (`src/openkos/cli/main.py`, `tests/unit/cli/test_ingest.py`; 598
   lines)
2. `5683a31` -- `docs(cli): document the --event-date ingest flag`
   (`docs/cli.md`; 1 line)

Staged only `src/openkos/cli/main.py`, `tests/unit/cli/test_ingest.py`,
and `docs/cli.md`, by explicit path -- no `git add -A` or `git add .`.
`openspec/changes/source-event-time/` remains untracked (per slice 1's
orchestrator note below; unchanged this run). Nothing pushed.

## Slice 3 Review Workload / Budget

`git diff --stat feat/1014-source-event-time-2...HEAD` across both
commits: **3 files changed, 599 insertions(+), 9 deletions(-) = 608
authored changed lines**, over tasks.md's own ~210-line forecast for this
slice and over the ~400-line advisory budget -- the same comment/
docstring-density pattern slices 1 and 2 reported (every new branch,
guard, and helper is documented at the density the surrounding function
already uses -- e.g. the convergence-condition comment block explaining
why `event_date.changed` narrows rather than widens the pre-#1014c skip,
and the 16 new CLI-level tests, which this slice's "first end-to-end
reachable" nature makes necessarily larger than slices 1-2's pure-function
tables). No code, comments, blank lines, or tests were trimmed to chase
the budget (explicitly disallowed by this run's constraints). Splitting
further was considered and rejected: `_ingest_single`'s option, refusals,
threading, preview, warning, and convergence-condition changes are all
edits to ONE function's control flow, landed together because splitting
them would leave intermediate commits with a broken or contradictory
`_ingest_single` body (e.g. a commit adding the flag but not the
convergence-condition narrowing would silently make `--event-date` a
no-op on every converged re-ingest, the exact defect design.md Decision 6
exists to prevent). Reported honestly as a `size:exception` candidate for
the orchestrator's delivery-strategy tracking, consistent with slices 1
and 2.

## Slice 3 deviations from design

None. Implementation matches design.md Decisions 3, 5, 6, and 7 exactly:
the two exit-2 refusals decided by input shape before any read (Decision
5), `event_date_flag`/`source_name` threaded through `_ingest_single` into
`compose_source_document` (Decision 3, landed in slice 2 and consumed
here), the convergence gate narrowed to `not source_plan.event_date.
changed` with the `stage_derived_objects(carried=converged)` short-circuit
and the `observability.stage_notice`/`_render_staged_derived_objects`
guards on `converged is None` (Decision 6), and the preview-line/warning
wording exactly as design.md's tables state (the delta specs left the
EXACT wording as design's recommendation per design.md's own Open
Questions note, so design's wording stands unmodified). One clarification
beyond design's literal text, spelled out during implementation: the
`spinner context stays as it is, entered and left at once` note in
Decision 6 was read as "construct `extraction_llm` and open/close the
`Console(...).status(...)` block unconditionally, even on the carried
path" -- confirmed safe because `stage_derived_objects(carried=...)`
returns before any `llm.chat` call, so the otherwise-unused spinner and
client construction are inert overhead, not a correctness risk.

## Post-merge (unchanged, still out of scope for sdd-apply)

Per `openspec/config.yaml`'s `rules.archive`, the archive phase merges the
three delta specs into their living specs and flips ADR-0023's status --
not performed by any run of `sdd-apply`, including this one.

## Orchestrator note

`openspec/changes/source-event-time/` is listed in `.git/info/exclude` (local) so the native review preflight does not demand an undocumented untracked-selection JSON. **Remove that line before the archive PR**, or the change folder cannot be staged.
