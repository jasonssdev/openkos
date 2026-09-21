# Tasks: partial-read-verb-reports

Strict TDD. Every behavioral task pairs a test written and observed RED with
the implementation task that turns it GREEN, in that order. `[TEST]` tasks
name the exact assertion and the exact reason it is RED today. `[IMPL]` tasks
say only what makes the paired `[TEST]` pass — no new behavior is introduced
in an `[IMPL]` task beyond what its paired test already pins.

## Verification (every `[IMPL]`/`[TEST]` task, before it is considered done)

Run these three commands directly — do not rely on `pre-commit`'s own
"Passed" summary, which has been observed green locally while
`ruff format --check .` failed on the same tree:

```
uv run pre-commit run --all-files
uv run ruff format --check .
uv run pytest --cov
```

CI gates at 90% branch coverage. `ruff check` alone is not the gate;
`ruff format --check` is enforced separately and must be run on its own.

## Mutation discipline for every new guard (D1–D4, L1–L3, and the D1→7/7b propagation branch below)

Each of the eight per-site containments — the seven raise-site guards plus
the D1→7/7b propagation branch (Decision 7: checks 7/7b consulting check
6's outcome) — gets mutated in **both** directions after it lands, before
it is considered proven:

1. **Narrow-direction**: replace the guard's `except` clause with a type that
   cannot match the injected exception (e.g. `except ZeroDivisionError`).
   Re-run the site's own containment test and confirm it goes RED — the
   original uncaught exception must resurface. Revert byte-exactly.
2. **Broad-direction**: widen the guard's `except` clause to bare
   `Exception`. Re-run the full test file that pins the site's documented
   "caller bug" exception (named per site below) and confirm THAT test goes
   RED — proving the narrow scope is load-bearing, not incidental. If no such
   test exists yet for the site, write it first (see each site's note) so
   this direction has something to falsify. Revert byte-exactly.

After every revert, delete `__pycache__` directories touched by the mutated
file before re-running the suite — a same-size mutation can otherwise run
against stale bytecode and produce a false verdict.

## Slicing

Two independently committable, independently green slices, in this order:
**Slice 1 (doctor)**, **Slice 2 (lint)**. Both slices are self-contained,
including their own exit-rule rewrite — see "Note on slice boundaries" at the
end for how to peel a third "shared exit vocabulary" PR out of them if the
combined diff exceeds the 400-line review budget.

---

## Slice 1 — `doctor`

### Shared primitive

- [x] **T1.1** [IMPL, no test — pure data shape] Create `src/openkos/read_outcome.py`
  as a leaf module importing nothing from `openkos`: `NOT_RUN: NotRunStatus`,
  `NotRunStatus = Literal["not-run"]`, and `NotRun` (frozen dataclass:
  `label: str`, `reason: str`). No behavior to pin RED against yet — the
  first test that exercises it is T1.3.

### Containment (D1–D4)

- [x] **T1.2** [IMPL] `application/doctor.py`: import `read_outcome`; widen
  `CheckResult.status`'s `Literal["pass", "fail", "skip"]` to include
  `read_outcome.NotRunStatus`.

- [x] **T1.3** [TEST] `tests/unit/application/test_doctor_service.py` — add
  `test_run_diagnostics_reports_not_run_when_survey_bundle_scan_torn_writes_or_scan_nesting_violations_raises`,
  parametrized over three patch targets: `openkos.model.okf.survey_bundle`,
  `openkos.bundle.ledger.scan_torn_writes`, `openkos.bundle.ledger.scan_nesting_violations`.
  Must run **in-workspace with real bundle content** (not the bare `tmp_path`
  the existing tests use) so checks 6/12/13 are actually reached — this is
  the "genuine counterpart to `test_doctor_service.py:117`" the proposal
  requires. The fixture MUST pre-create `.openkos/vectors.db` and
  `.openkos/fts.db` (empty files, mirroring
  `test_doctor_all_healthy_exits_zero`'s setup) — this isolates D1/D2/D3's
  own not-run reporting from Decision 7's downstream propagation, which
  would otherwise also turn checks 7/7b not-run on the `survey_bundle`
  parametrization (their `index_path.exists()` branch short-circuits
  before ever consulting `bundle_emptiness`, per Decision 7). Asserts:
  `run_diagnostics` returns without raising, `len(results) == 15`, exactly
  one `CheckResult` has `status == read_outcome.NOT_RUN` and its `detail`
  contains the raised message, the other 14 keep their ordinary status.
  **RED today**: the patched exception propagates out of `run_diagnostics`
  uncaught — the test fails with an unhandled exception, not an assertion
  mismatch. Decision 7's cascade case (no index files present) is a
  separate scenario, covered by T1.6a.

- [x] **T1.4** [TEST] same file — add
  `test_run_diagnostics_reports_the_integrity_check_as_not_run_when_reset_point_available_raises`.
  GIVEN an in-workspace bundle with a flagged nesting violation and a
  `reset_point_available` thunk that raises `application_doctor.ProbeUnavailable("git broke")`
  when called, THEN `len(results) == 15` and the "Merge ledger entries free
  of post-merge mutation" check has `status == read_outcome.NOT_RUN` with
  `"git broke"` in `detail`. **RED today**: `ProbeUnavailable` does not exist
  and the call site does not catch it — the thunk's raise propagates
  uncaught.

- [x] **T1.5** [OPTIONAL — reviewer decides, unforecast scope] **Declined** by
  the apply agent (no reviewer available to opt in during this run; this is
  explicitly unforecast scope the task list does not decide). T1.7 below
  defines its own local tuple with the same three classes instead. Rename
  `src/openkos/bundle/ledger.py`'s `_SIDECAR_SKIP_ERRORS` → `SIDECAR_SKIP_ERRORS`
  (drop the leading underscore; ~5 lines, no test currently references the
  private name). This lets T1.7 import and reuse the tuple directly instead
  of duplicating `(OSError, ValueError, yaml.YAMLError)` inside
  `application/doctor.py`. **If declined**, T1.7 defines its own local tuple
  with the same three classes instead of reaching across the layer boundary
  for a private name — functionally identical, ~5 more duplicated lines.
  This is scope the proposal did not forecast; it is not decided here.

- [x] **T1.6** [IMPL] `application/doctor.py`: wrap the `okf.survey_bundle(...)`
  call at doctor.py:453 (D1) in `try: ... except OSError as exc:`; on catch,
  append `CheckResult("Bundle readable", read_outcome.NOT_RUN, critical=False, detail=str(exc))`
  instead of the pass/fail branch, and leave `bundle_empty` (still the
  plain `bool`, unchanged in this task — T1.6b below is what retypes it)
  at its `False` initializer. `bundle_dot_directory`'s `ValueError` is a
  documented caller bug and must NOT be caught here. Makes T1.3's
  `survey_bundle` case GREEN. **Deliberately leaves Decision 7's gap open**:
  checks 7/7b still read the unretyped `bundle_empty`, so they still take
  their `False`-reads-as-"nonempty" `else` branch — this is exactly the gap
  T1.6a below pins RED, closed by T1.6b, not by this task.

- [x] **T1.6a** [TEST] `tests/unit/application/test_doctor_service.py` — add
  `test_run_diagnostics_reports_the_index_checks_as_not_run_when_bundle_readable_did_not_run`
  (Decision 7). Patch `openkos.model.okf.survey_bundle` to raise, same
  trigger as T1.3, but in a workspace where `.openkos/vectors.db` and
  `.openkos/fts.db` do **not** exist (the opposite fixture from T1.3's own
  pre-created-index-files setup — this is what isolates the two tests).
  Asserts: the "Bundle readable", "Workspace vector index present", and
  "Workspace FTS index present" `CheckResult`s all have
  `status == read_outcome.NOT_RUN`, and the latter two each carry a
  `detail` naming "Bundle readable" as the unmet dependency. **RED today**:
  once T1.6 lands (and before T1.6b), `bundle_empty` is still the plain
  `bool` default `False`, which checks 7/7b's unmodified `elif
  bundle_empty: skip else: fail` reads as "nonempty" — both report
  `status == "fail"` with `remediation == "openkos reindex"`, the exact
  false-healthy-adjacent bug this decision exists to close.

- [x] **T1.6b** [IMPL] `application/doctor.py`: retype `bundle_empty`
  (doctor.py:450) to `bundle_emptiness: Literal["empty", "nonempty"] |
  read_outcome.NotRunStatus`, initialized to `read_outcome.NOT_RUN` (design.md
  Decision 7), and update the truthful assignment at :456 to set
  `"empty"`/`"nonempty"` instead of `True`/`False`; T1.6's `except` branch
  leaves it at its `NOT_RUN` initializer (no change needed there — it
  already sets nothing on that path). At checks 7 and 7b (doctor.py:495,
  :533), insert a `bundle_emptiness == read_outcome.NOT_RUN` arm before the
  existing `elif`/`else`: on match, append `CheckResult(label,
  read_outcome.NOT_RUN, critical=False, detail="depends on check 6 (Bundle
  readable)")` instead of evaluating `skip`/`fail`. The `if
  index_path.exists(): ... pass` arm is untouched — it never reads
  `bundle_emptiness`. The existing `elif bundle_emptiness == "empty": skip`
  / `else: fail` arms are otherwise unchanged, now reached only when
  `bundle_emptiness` is `"empty"`/`"nonempty"`. This task retypes the
  declaration, the assignment, and both consultation sites together, in one
  commit-sized step — never landing an intermediate state where the
  retyped default (`read_outcome.NOT_RUN`, a truthy string) is read by the
  still-old two-way branch, which would silently flip the wrong answer from
  `fail` to `skip` instead of fixing it. Makes T1.6a GREEN.

- [x] **T1.6c** [TEST, both directions] Mutate T1.6b's guard per the
  mutation-discipline section above.
  **Narrow-direction**: remove the `bundle_emptiness == read_outcome.NOT_RUN`
  arm (fall back to the old two-way branch). Re-run T1.6a and confirm RED
  — checks 7/7b report `fail`/`openkos reindex` again.
  **Broad-direction**: widen the new arm to also match `"empty"` (i.e.
  `if bundle_emptiness != "nonempty":`). Re-run the existing
  `test_doctor_index_checks_skip_on_an_empty_bundle_recommending_ingest`
  (`tests/unit/cli/test_doctor.py:1540`) and confirm it goes RED — an
  actually-empty, successfully-surveyed bundle must still report `skip`,
  not `not-run`; this proves the exact-sentinel match is load-bearing, not
  incidental. Revert both mutations byte-exactly; purge `__pycache__` for
  `application/doctor.py` before each re-run.

- [x] **T1.7** [IMPL] `application/doctor.py`: wrap `bundle_ledger.scan_torn_writes(...)`
  (doctor.py:665, D2) and `bundle_ledger.scan_nesting_violations(...)`
  (doctor.py:709, D3) each in their own `try/except`, catching
  `bundle_ledger.SIDECAR_SKIP_ERRORS` (or the local duplicate tuple per
  T1.5's outcome); on catch, append the respective check's `CheckResult`
  with `read_outcome.NOT_RUN` and `detail=str(exc)`. Makes T1.3's other two
  cases GREEN.

- [x] **T1.8** [IMPL] `application/doctor.py`: add
  `class ProbeUnavailable(Exception)` (mirrors `application/lint.py::LintInputUnavailable`'s
  shape). Wrap the `reset_point_available()` call at doctor.py:711 (inside
  the `if violations:` branch, D4) in `try/except ProbeUnavailable as exc:`,
  appending the integrity check's `CheckResult` with `read_outcome.NOT_RUN`
  and `detail=str(exc)`. In `cli/main.py`, change the `_reset_point_available`
  thunk (~line 15014) so its body catches `vcs_git.GitError` and re-raises
  `application_doctor.ProbeUnavailable(str(exc)) from exc` — `application/*`
  may not import `openkos.vcs` (AST-enforced in
  `tests/unit/application/test_layering.py`), so the translation happens at
  the CLI boundary. Makes T1.4 GREEN.

- [x] **T1.9** [TEST, both directions] Mutate D1's guard (T1.6) per the
  mutation-discipline section above. Broad-direction tripwire: confirm (or,
  if none exists, add)
  `test_run_diagnostics_lets_a_bundle_dot_directory_value_error_propagate_uncaught`,
  pinning that a `ValueError` from `bundle_dot_directory` still raises
  uncaught through `run_diagnostics` — this is the test that must go RED
  when the guard is widened to bare `Exception`.

- [x] **T1.10** [TEST, both directions] Mutate D2/D3's guard (T1.7) per the
  mutation-discipline section, narrow-direction only. **Gap**: unlike D1, no
  site-specific "caller bug" exception class is documented for D2/D3 — the
  design's own class list (`OSError`, `ValueError`, `yaml.YAMLError`) is
  already the full set that legitimately means "corrupt sidecar" at these
  two sites. The broad-direction tripwire is therefore unproven; flag this
  for reviewer confirmation rather than fabricating a negative case that the
  design does not support.

- [x] **T1.11** [TEST, both directions] Mutate D4's guard (T1.8). Broad-
  direction tripwire: inject a `reset_point_available` thunk that raises an
  unrelated exception (e.g. `RuntimeError`, simulating a bug in the thunk
  itself rather than a git failure) and assert it still propagates uncaught
  through `run_diagnostics` when the guard correctly narrows to
  `ProbeUnavailable`; confirm this assertion goes RED when the guard is
  widened to bare `Exception`.

### Render (Decision 5) — before the exit rule, so the headline test's
### observed value is a real `0`, not a `KeyError` crash

- [x] **T1.12** [TEST] `tests/unit/cli/test_doctor.py` — add
  `test_doctor_renders_not_run_and_counts`. GIVEN `okf.survey_bundle` patched
  to raise, in a workspace with no `.openkos/vectors.db` or `.openkos/fts.db`
  (i.e. the same fixture shape as T1.6a, not T1.3 — this scenario is meant
  to exercise Decision 7's cascade at the CLI layer), THEN CLI stdout
  contains `[NOT RUN] Bundle readable — <reason>`, `[NOT RUN] Workspace
  vector index present — depends on check 6 (Bundle readable)`, `[NOT RUN]
  Workspace FTS index present — depends on check 6 (Bundle readable)`, and
  the line `12 check(s) completed, 3 did not run.` — **revised from an
  earlier `14`/`1` draft of this test**: once Decision 7 lands, the same
  `survey_bundle` trigger this test already used was always going to
  cascade into checks 7/7b too, so the original count was wrong for the
  fixture it described, not merely stale. **RED today**: `_render_check`'s
  tag dict has no `"not-run"` key, so once T1.2/T1.6/T1.6b have landed a
  `"not-run"` status reaches `_render_check` and raises `KeyError` during
  rendering — the CLI invocation crashes instead of printing the expected
  lines.

- [x] **T1.13** [TEST] add
  `test_render_check_has_a_tag_for_every_status_literal` (drift guard). Reads
  every member of `application_doctor.CheckResult`'s `status` `Literal` (via
  `typing.get_args`/`get_type_hints`, whichever resolves against this
  module's `from __future__ import annotations` usage — confirm which
  before writing) and asserts each one is a key in `_render_check`'s tag
  dict. **RED today**: the dict is `{"pass": ..., "fail": ..., "skip": ...}`,
  missing `"not-run"`.

- [x] **T1.14** [IMPL] `cli/main.py`: `_render_check`'s tag dict gains
  `"not-run": "[NOT RUN]"`. After the render loop in `doctor()`, add
  `n = sum(1 for r in results if r.status == read_outcome.NOT_RUN)` and
  `typer.echo(f"{len(results) - n} check(s) completed, {n} did not run.")`.
  Makes T1.12 and T1.13 GREEN.

### Exit rule (Decision 4, doctor half)

- [x] **T1.15** [TEST] add
  `test_doctor_exits_two_when_a_check_did_not_run_and_every_critical_passes`.
  GIVEN a check reports not-run (T1.6's containment) and every critical
  check passes, THEN `exit_code == 2`. **Run it now, before touching the
  exit predicate, and record the observed failure as `assert 0 == 2`** — this
  is the proposal's stated top mitigation; observing it fail here is what
  makes the mitigation real rather than theatre. Do not proceed to T1.16
  until this failure has been observed and recorded.

- [x] **T1.16** [IMPL] `cli/main.py`: rewrite the exit predicate at
  doctor.py:15041 from `any(r.status == "fail" and r.critical for r in results): exit(1)`
  to:
  ```python
  critical_failed = any(r.status == "fail" and r.critical for r in results)
  incomplete = any(r.status == read_outcome.NOT_RUN for r in results)
  if critical_failed:
      raise typer.Exit(code=1)
  if incomplete:
      raise typer.Exit(code=2)
  ```
  Makes T1.15 GREEN.

- [x] **T1.17** [TEST] add
  `test_doctor_exits_one_when_not_run_coexists_with_a_critical_failure`.
  GIVEN one check reports not-run AND a critical check fails, THEN
  `exit_code == 1`, not `2`. This passes immediately under the new rule
  (critical dominates) — its falsifiability is proven by T1.18, not by this
  test alone.

- [x] **T1.18** [TEST, falsifiability] Swap the two `if` blocks in T1.16's
  rewritten predicate (`incomplete` check first, `critical_failed` check
  second) and re-run T1.17: confirm it goes RED (`exit_code == 2` instead of
  the expected `1`). Revert the swap byte-exactly, delete `__pycache__` for
  `cli/main.py`, and re-run T1.17 to confirm it is GREEN again on the
  original ordering.

### Layering docstring

- [x] **T1.19** [IMPL] `application/__init__.py`: update its docstring to
  name `read_outcome` among the modules `application/*` may legally import
  (it is a leaf, not part of `application/`).

---

## Slice 2 — `lint`

### `LintReport.not_run`

- [ ] **T2.1** [TEST] add `test_lint_report_not_run_is_empty_on_a_complete_run`
  in the `lint.py`/`application/lint.py` test suite (co-locate with existing
  `LintReport` construction tests). GIVEN a run where every late walk
  completes, THEN `report.not_run == ()`. **RED today**: `LintReport` has no
  `not_run` field — `AttributeError`.

- [ ] **T2.2** [IMPL] `src/openkos/lint.py`: add
  `not_run: tuple[NotRun, ...] = ()` to `LintReport`, importing `NotRun` from
  `read_outcome` (T1.1). A `tuple` with no `default_factory`, unlike its 14
  `list` siblings — it is never mutated, mirroring `run_diagnostics`' own
  `tuple[CheckResult, ...]` return shape. Makes T2.1 GREEN.

### Containment (L1–L3)

- [ ] **T2.3** [TEST] `tests/unit/cli/test_lint.py` — rename
  `test_lint_lets_a_late_name_walk_failure_propagate_uncaught` (line ~1113)
  to `test_lint_reports_a_late_name_walk_failure_as_not_run_without_losing_findings`.
  Keep the exact `Path.rglob` monkeypatch trigger. Replace the assertions
  with:
  ```python
  assert result.exit_code == 2                                  # was `!= 0`
  assert not isinstance(result.exception, OSError)               # no longer escapes
  assert "simulated unreadable subdirectory" in result.stdout    # reason moved to data
  assert "Stale stamps:" in result.stdout                        # 11 surviving lists render
  assert "12 check(s) completed, 1 did not run." in result.stdout
  assert "failed while reading the workspace" not in result.stderr  # kept
  ```
  **RED today, on every assertion**: the `OSError` propagates uncaught,
  `exit_code` reflects the crash (not `2`), `result.exception` IS the
  `OSError`, and neither the counts line nor the surviving `Stale stamps:`
  section exists.

- [ ] **T2.4** [TEST] add `test_lint_reports_a_state_dir_walk_failure_as_not_run_without_losing_findings`.
  Do NOT reuse L1's `Path.rglob` patch (it also breaks `collect_docs` →
  `LintInputUnavailable` → the wrong exit path). Patch
  `openkos.lint.check_state_dir_contains_no_markdown` directly to raise
  `OSError("simulated unreadable state dir")` — valid because
  `application/lint.py` resolves it as a module attribute at call time.
  Same assertion shape as T2.3 (adjust the surviving-count line to match).
  **RED today**: uncaught `OSError`.

- [ ] **T2.5** [TEST] add `test_lint_reports_a_dot_dir_walk_failure_as_not_run_without_losing_findings`,
  same shape as T2.4 but patching `openkos.lint.check_dot_dir_markdown`.
  **RED today**: uncaught `OSError`.

- [ ] **T2.6** [TEST] add `test_total_checks_matches_the_number_of_check_calls_in_build_lint_report`
  (AST drift guard, same style as `tests/unit/application/test_layering.py`):
  parse `application/lint.py`'s AST, count `lint_check.check_*`/`scan_*` call
  sites inside `build_lint_report`, and assert the count equals
  `application_lint.TOTAL_CHECKS`. **RED today**: `TOTAL_CHECKS` does not
  exist — `AttributeError`.

- [ ] **T2.7** [IMPL] `application/lint.py`: add `TOTAL_CHECKS: Final = 13`
  (13 `check_*`/`scan_*` calls; `check_below_source_sensitivity` feeds both
  `below_source` and `multi_source_uncovered`, which is why `LintReport`
  declares 14 finding fields against 13 calls). Makes T2.6 GREEN.

- [ ] **T2.8** [IMPL] `application/lint.py`: wrap the
  `lint_check.check_non_nfc_names(layout.bundle_dir)` call (L1) in
  `try/except OSError as exc:`; on catch, append
  `NotRun(label="Non-NFC names", reason=str(exc))` to a local `not_run` list
  and set `non_nfc = []` (or leave whatever partial result the check
  returned before raising — confirm against `check_non_nfc_names`'s actual
  raise point) instead of propagating. `relative_to`'s `ValueError` stays
  raising, uncaught. Pass the accumulated `not_run` tuple into the returned
  `LintReport`. Contributes to making T2.3 GREEN.

- [ ] **T2.9** [IMPL] `application/lint.py`: same pattern (L2) around
  `lint_check.check_state_dir_contains_no_markdown(layout.bundle_dir)`.
  Contributes to making T2.4 GREEN.

- [ ] **T2.10** [IMPL] `application/lint.py`: same pattern (L3) around
  `lint_check.check_dot_dir_markdown(layout.bundle_dir)`. Contributes to
  making T2.5 GREEN.

- [ ] **T2.11** [TEST, both directions] Mutate L1's guard (T2.8) per the
  mutation-discipline section. Broad-direction tripwire: confirm (or add) a
  test pinning that `check_non_nfc_names`'s `relative_to`-derived `ValueError`
  still propagates uncaught when the guard correctly narrows to `OSError`.

- [ ] **T2.12** [TEST, both directions] Mutate L2's guard (T2.9), same
  tripwire shape for `check_state_dir_contains_no_markdown`.

- [ ] **T2.13** [TEST, both directions] Mutate L3's guard (T2.10), same
  tripwire shape for `check_dot_dir_markdown`.

### Render and exit rule (Decision 5/4, lint half)

- [ ] **T2.14** [IMPL] `cli/main.py`'s `lint()`: add a `Checks that did not
  run:` section, rendered **first** — after `report.notices`, before the
  `Stale stamps:` block — one line per `NotRun` entry
  (`f"  {nr.label}: {nr.reason}"`), with an empty-state line when
  `report.not_run` is empty. After the existing render blocks (end of the
  function), add the counts line:
  `f"{application_lint.TOTAL_CHECKS - len(report.not_run)} check(s) completed, {len(report.not_run)} did not run."`.
  Contributes to making T2.3/T2.4/T2.5's stdout assertions GREEN.

- [ ] **T2.15** [IMPL] `cli/main.py`'s `lint()`: after all rendering, add
  `if report.not_run: raise typer.Exit(code=2)`. Findings alone (the other
  11 fields) never gate; only a non-empty `not_run` does. Makes the
  remaining `exit_code == 2` assertions in T2.3/T2.4/T2.5 GREEN — this is
  the point at which all three tests reach full green.

---

## Note on slice boundaries

The proposal's contingency ("if the forecast exceeds [400 lines], slice as
doctor first, then lint, then the shared exit vocabulary") is not forced
here: the design's own RED-ordering ties each verb's exit-rule work directly
to that verb's own containment/render tests (T1.15–T1.18 depend on T1.6's
containment already existing; T2.15 depends on T2.8–T2.10's containment), so
splitting exit-code work out as an upfront third slice would leave an
intermediate slice shipping a working `[NOT RUN]` render with a **silently
wrong** exit code — precisely the false-healthy risk this change exists to
close. Both slices as structured above are fully green on their own.

If Slice 1 or Slice 2 alone still exceeds budget, the mechanical cut is:
peel **T1.15–T1.18** (doctor's exit-rule tests + rewrite + falsifiability
check) into a follow-on PR on top of an already-merged Slice 1 minus those
four tasks, and/or peel **T2.15** (plus the `exit_code == 2` assertions in
T2.3–T2.5, temporarily loosened to whatever Slice 2 minus T2.15 actually
produces) into a follow-on PR on top of Slice 2. This is why T1.15–T1.18 and
T2.15 are called out as their own subsections above rather than folded into
the containment tasks.

## Explicitly out of scope for this task list

- #1002 items B, D, E, F, beyond the one in-workspace `doctor` counterpart
  (T1.3/T1.4) that item A's own behavior requires.
- The `doctor-command` spec's "ten `CheckResult`s" banner-count drift against
  the tested fifteen — pre-existing, left for the reviewer.
- `docs/adr/0022-*.md` and its `docs/adr/README.md` index row — already
  present on this branch (untracked/modified per current git status); no
  task recreates them. ADR status moves to `Accepted` at merge, per
  `AGENTS.md`, not as an implementation task here.
