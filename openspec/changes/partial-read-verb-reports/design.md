# Design: partial-read-verb-reports

## Technical Approach

One leaf module names the not-run fact. `doctor` widens `CheckResult.status`
with that module's token; `lint` gains `LintReport.not_run`. Each of the seven
raise sites gets its own `except` naming real classes. Both exit rules are
rewritten, never inherited.

## Architecture Decisions

### Decision 1: the shared primitive is `src/openkos/read_outcome.py`, NOT `application/`

**Choice**: a new leaf module importing nothing from `openkos`, peer of
`fsio.py`/`lock.py`:

```python
NotRunStatus = Literal["not-run"]
NOT_RUN: NotRunStatus = "not-run"          # the token's ONE spelling

@dataclass(frozen=True)
class NotRun:
    label: str      # which check
    reason: str     # str(exc) of what it raised
```

**Why it moved off the proposal's `application/`**: `LintReport` lives in
`src/openkos/lint.py`, and the approved lint spec requires the field *on that
dataclass*. The import direction is therefore forced:

```
application/doctor.py ─┐
application/lint.py ───┼──→ read_outcome (leaf, imports nothing)
openkos/lint.py     ───┘          ▲
     ▲                            │
     └── application/lint.py ──────┘  (already imports openkos.lint)
```

`application/__init__.py` states the rule verbatim: *"nothing in those packages
may import `openkos.application`"*. Putting the primitive under `application/`
would make `openkos/lint.py` import upward and close the cycle
`application.lint → openkos.lint → application.*`. A leaf keeps every edge
downward, adds no canonical→derived edge, and needs no exception to a shipped
rule. This is the one deliberate deviation from the proposal's wording; the
proposal's *intent* — one shared vocabulary, one import, no per-verb dialect —
is preserved exactly.

### Decision 2: `GitError` is translated at the adapter boundary

`tests/unit/application/test_layering.py::test_application_modules_never_import_cli_typer_or_rich`
bans `openkos.vcs` from `application/*` **unconditionally, by AST**. So
`application/doctor.py` cannot catch `vcs_git.GitError` by name. It gains
`class ProbeUnavailable(Exception)` — the exact shape
`application/lint.py::LintInputUnavailable` already uses one module over — and
`cli/main.py::_reset_point_available` raises it `from` the `GitError`. The
thunk still raises, the service still catches, the reason still reaches
`detail`. See "Spec deviation" below.

### Decision 3: per-site containment (no bare `except Exception`)

| # | Site | Catches | Why exactly that |
|---|---|---|---|
| D1 | `okf.survey_bundle` (doctor.py:453) | `OSError` | `rglob`/`is_dir` only; per-file reads and `frontmatter.loads` are already guarded inside. `bundle_dot_directory`'s `ValueError` is a *documented caller bug* — left raising. |
| D2 | `scan_torn_writes` (:665) | `bundle_ledger.SIDECAR_SKIP_ERRORS` | `iter_pending` rglob (`OSError`), `read_text` (`OSError`/`UnicodeDecodeError ⊂ ValueError`), unguarded `load_frontmatter` (`yaml.YAMLError`). |
| D3 | `scan_nesting_violations` (:709) | `bundle_ledger.SIDECAR_SKIP_ERRORS` | `iter_ledgers` sits *outside* the inner guard (`OSError`); same class list, one definition. |
| D4 | `reset_point_available()` (:711) | `ProbeUnavailable` | Decision 2. |
| L1 | `check_non_nfc_names` | `OSError` | spec's named class; `relative_to`'s `ValueError` stays raising. |
| L2 | `check_state_dir_contains_no_markdown` | `OSError` | same. |
| L3 | `check_dot_dir_markdown` | `OSError` | same. |

Rule: **contain the environment's failure, never the programmer's.** D2/D3
include `ValueError` because there it means *corrupt sidecar* — the class
`ledger.py` itself already treats as data. Rename `_SIDECAR_SKIP_ERRORS` →
`SIDECAR_SKIP_ERRORS` (no test references it) rather than reach a private
across layers.

### Decision 4: the rewritten exit rules

```python
# doctor — precedence is the whole decision
critical_failed = any(r.status == "fail" and r.critical for r in results)
incomplete      = any(r.status == read_outcome.NOT_RUN for r in results)
if critical_failed:  raise typer.Exit(code=1)   # dominates
if incomplete:       raise typer.Exit(code=2)
# lint
if report.not_run:   raise typer.Exit(code=2)   # findings never gate
```

The four-row matrix falls out: healthy → fall through (`0`); informational
fail → both predicates False (`0`); not-run, criticals pass → `2`; not-run +
critical fail → `1`. Decision 7's propagation changes how many `results`
entries can carry `not-run` (up to three instead of one, per raise site),
never this predicate: `incomplete` is still `any(...)` over the same field.

### Decision 5: render

`_render_check`'s tag map gains `"not-run": "[NOT RUN]"`; the reason rides in
`detail`, so `[NOT RUN] Bundle readable — <reason>` needs no second branch, and
the `-> remediation` line stays `fail`-only. A `dict` miss would be a silent
`KeyError`, so a drift test asserts every `get_args` member of the status
`Literal` has a tag. Counts: doctor prints
`f"{len(results) - n} check(s) completed, {n} did not run."` after the lines —
`skip` counts as *completed*, matching the exit rule so line and code agree.
`lint` prints a `Checks that did not run:` section **first** (after notices,
before `Stale stamps:` — a partial report must announce itself before its
content) and the same counts line last, over
`application/lint.TOTAL_CHECKS: Final = 13` (13 `check_*` calls, 14 fields;
`check_below_source_sensitivity` feeds two) pinned by an AST drift guard.

### Decision 6: `LintReport.not_run: tuple[NotRun, ...] = ()`

A `tuple` (per proposal) though its 14 siblings are `list` + `default_factory`:
it is never mutated, `()` needs no factory, and it mirrors
`run_diagnostics`' own `tuple[CheckResult, ...]`.

### Decision 7: checks 7/7b propagate check 6's not-run instead of defaulting a bool

**The gap.** `bundle_empty` (doctor.py:450) is a plain `bool` initialized
`False` and only set truthfully at :456, after `okf.survey_bundle` returns.
Once D1 (Decision 3) lets that call fail into a `not-run` `CheckResult`
instead of raising, `bundle_empty` never gets past its `False` initializer.
Checks 7 and 7b (:495, :533) each branch `elif bundle_empty: skip else:
fail(remediation="openkos reindex")` — so a `not-run` bundle read is
silently read as "non-empty bundle", and both dependent checks report
`[FAIL] -> openkos reindex` about a bundle nobody could survey. `bundle_empty`
needs a third meaning, "unknown, because check 6 did not run", and the type
must make that meaning impossible to lose by accident.

**Choice: retype the variable, don't add `None`.** `bundle_empty: bool` becomes

```python
bundle_emptiness: Literal["empty", "nonempty"] | read_outcome.NotRunStatus
bundle_emptiness = read_outcome.NOT_RUN  # unknown until check 6 succeeds
...
except OSError as exc:
    results.append(CheckResult("Bundle readable", read_outcome.NOT_RUN, ..., detail=str(exc)))
    # bundle_emptiness stays read_outcome.NOT_RUN — nothing to set here
else:
    bundle_emptiness = "empty" if (not survey.findings and survey.sources == 0
                                    and survey.concepts == 0) else "nonempty"
    ...
```

and checks 7/7b's branch becomes:

```python
if index_path.exists():
    ...  # pass — unchanged, never consults bundle_emptiness
elif bundle_emptiness == read_outcome.NOT_RUN:
    results.append(CheckResult(label, read_outcome.NOT_RUN, critical=False,
                                detail="depends on check 6 (Bundle readable)"))
elif bundle_emptiness == "empty":
    ...  # skip — unchanged
else:  # "nonempty"
    ...  # fail + "openkos reindex" — unchanged
```

**Rejected: `bool | None` with `None` as unknown.** This is the obvious
minimal diff, and it is exactly the shape that created the original bug:
MyPy strict does not flag `if bundle_empty:` on a `bool | None` — `None` is
falsy, so a stray truthy check silently reproduces the same "unknown reads
as known-false" collapse this decision exists to close, just one type
smaller. It does not pass the brief's "unrepresentable-by-accident" bar; it
passes MyPy strict without ever exercising the new state's meaning.

**Rejected: a bespoke enum/type for bundle emptiness.** A dedicated
three-member type (`BundleEmptiness.EMPTY` / `.NONEMPTY` / `.UNKNOWN`) would
also work and would be marginally more self-documenting, but it duplicates
vocabulary this change already introduced: `read_outcome.NotRunStatus` /
`read_outcome.NOT_RUN` already name "a producer did not run", and the
proposal's own stated goal is "an MCP adapter reads one word across both
verbs" — a second word for the identical fact, scoped to one local
variable, contradicts that. Reusing `NotRunStatus` costs nothing: `doctor.py`
already imports `read_outcome` (T1.2).

**Rejected: derive it from check 6's recorded `CheckResult` instead of a
separate variable.** Checks 7/7b need two facts check 6's `CheckResult`
does not carry structurally: whether the bundle is *empty*, not merely
whether it is *readable* (a `pass` status covers both empty and non-empty
bundles), and that distinction lives only in the local `survey` object.
Parsing it back out of `CheckResult.detail`'s human string
(`f"{survey.sources} sources, {survey.concepts} concepts"`) would be
fragile string-parsing across a layer boundary for data the function
already has in hand. Reading check 6's `status` field alone (to detect
`not-run`) is a strict subset of what the local variable already gives for
free.

**Why this is not a narrowing of the approved rule.** The `index_path.exists()`
branch is untouched deliberately: it answers a plain filesystem probe and
never reads `bundle_emptiness`, so it is not "a check whose input came from
a check that did not run" in the first place — the approved principle does
not reach it. Applying the rule to that branch too would turn a true `pass`
(the index file demonstrably exists) into a false `not-run`, which the
proposal's own risk register calls out as the thing to avoid in the other
direction (a false-healthy signal) — here it would be a false-*unhealthy*
one, an equally wrong report about a workspace this command exists to
diagnose accurately.

**No new import edges.** `application/doctor.py` already imports
`read_outcome` (T1.2); reusing `NotRunStatus`/`NOT_RUN` for
`bundle_emptiness` adds no import. `application/` still never imports
`openkos.vcs` — this decision does not touch the git-dependent checks
(6/12/13 are check 6 and the two merge-ledger checks; checks 7/7b are pure
filesystem probes plus this variable).

## Testing Strategy (strict TDD — RED order is load-bearing)

| # | Test written first | Asserts while RED |
|---|---|---|
| 1 | `test_doctor_service.py` in-workspace counterpart (D1/D2/D3, patching `openkos.model.okf.survey_bundle` etc.) | 15 results returned, one `status == "not-run"` — today the OSError escapes `run_diagnostics` |
| 1b | `test_doctor_service.py` — checks 7/7b propagation (Decision 7), patching `survey_bundle` with no `.openkos/vectors.db`/`fts.db` present | all three of bundle-readable, workspace-vector-index-present, workspace-fts-present return `status == "not-run"`; **observed today: the two dependent checks return `status == "fail"`, `remediation == "openkos reindex"`** — `bundle_emptiness` still defaults to the "nonempty" reading |
| 2 | **headline** `test_doctor_exits_two_when_a_check_did_not_run_and_every_critical_passes` | `exit_code == 2`; **observed `0`** — the false-healthy regression, seen before the rule is touched |
| 3 | `test_doctor_exits_one_when_not_run_coexists_with_a_critical_failure` | `exit_code == 1`. Passes on the old rule, so its falsifiability is proven by swapping the two `if`s in Decision 4 and observing RED |
| 4 | `test_doctor_renders_not_run_and_counts` | `[NOT RUN]` on all three of bundle-readable/vector-index/FTS-index, `12 check(s) completed, 3 did not run` — **revised from `14`/`1`**: the same fixture that raises inside `survey_bundle` now cascades into checks 7/7b under Decision 7, so this scenario was always exercising the propagated case, its originally-recorded count was just wrong |
| 5 | rewritten `test_lint.py` (L1) + two new (L2/L3) | below |
| 6 | `test_lint_report_not_run_is_empty_on_a_complete_run` | `report.not_run == ()` |

Row 1b's RED order matters the same way row 4's does: it asserts only on
the returned `CheckResult` tuple, never on rendered text, so it does not
depend on `_render_check` understanding `"not-run"` and can land directly
after row 1, before any CLI-level test. Row 4, by contrast, asserts on
`result.stdout` — `_render_check`'s tag lookup at `cli/main.py:14884` is a
bare `dict[...][r.status]`, so it MUST understand `"not-run"` before any
test can observe rendered output or an exit code; a propagation test
written against stdout before that lookup is fixed observes a `KeyError`
crash, not a wrong count. Row 1b (service-level) and Decision 7's
implementation are therefore sequenced before the render fix; row 4's
revised counts are only observable after it.

Test 2 must be recorded as `assert 0 == 2` or the proposal's top mitigation is
theatre. L2/L3 cannot reuse L1's `Path.rglob` patch (pattern `*.md` also breaks
`collect_docs` → `LintInputUnavailable` → exit 1, the wrong path): patch
`openkos.lint.check_state_dir_contains_no_markdown` /
`check_dot_dir_markdown` — valid because `application/lint.py` resolves them as
module attributes at call time.

### The `test_lint.py:1113` replacement (derived, not flipped)

`test_lint_lets_a_late_name_walk_failure_propagate_uncaught` is **renamed** to
`test_lint_reports_a_late_name_walk_failure_as_not_run_without_losing_findings`
— same `Path.rglob("*")` monkeypatch and trigger, inverted verdict, one edit so
the diff *is* the contract change. New assertions:

```python
assert result.exit_code == 2                                  # was `!= 0`; `!= 0` would pass on a crash (click exits 1)
assert not isinstance(result.exception, OSError)              # the OSError no longer escapes
assert "simulated unreadable subdirectory" in result.stdout   # was `in str(result.exception)` — the reason moved from traceback to data
assert "Stale stamps:" in result.stdout                       # the 11 surviving lists render — the point of the change
assert "12 check(s) completed, 1 did not run." in result.stdout
assert "failed while reading the workspace" not in result.stderr  # KEPT: a not-run must not be misreported as an input-read failure
```

`isinstance(result.exception, OSError)` has no positive counterpart: nothing
raises. Its information splits in two — the *containment* (assertion 2) and the
*reason's survival* (assertion 3). `exit_code == 2` is the load-bearing one: it
alone excludes a crash, and it is RED today at `1`.

## File Changes

| File | Action |
|---|---|
| `src/openkos/read_outcome.py` | Create — `NOT_RUN`, `NotRunStatus`, `NotRun` |
| `src/openkos/application/doctor.py` | Widen `status` `Literal`; 4 guards; `ProbeUnavailable`; retype `bundle_empty` → `bundle_emptiness` (Decision 7) and propagate into checks 7/7b |
| `src/openkos/bundle/ledger.py` | `_SIDECAR_SKIP_ERRORS` → public |
| `src/openkos/application/lint.py` | 3 guards; `TOTAL_CHECKS`; populate `not_run` |
| `src/openkos/lint.py` | `LintReport.not_run` |
| `src/openkos/cli/main.py` | `_render_check` branch; 2 count lines; 2 exit rules; `_reset_point_available` translates `GitError` |
| `src/openkos/application/__init__.py` | Docstring: name `read_outcome` among the legal leaf imports |
| `docs/adr/0022-*.md`, `docs/adr/README.md` | ADR + index row |
| `tests/unit/{cli/test_lint.py,cli/test_doctor.py,application/test_doctor_service.py,test_lint*.py}` | per the table above |

## Spec deviation — read this

`doctor-command/spec.md`'s modified requirement names **`vcs_git.GitError`** as
the class the service sees. It is implementable as *behaviour* (a `GitError`
from git makes check 13 print `[NOT RUN]` with the reason, end to end) but not
as *literal class identity*: the layering guard forbids that import. The
observable scenario passes unchanged; a test written against the scenario
monkeypatches `vcs_git.has_reset_point` to raise `GitError` and asserts
`[NOT RUN]`. Nothing else in either spec was unimplementable.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary is added. `doctor`'s existing
`git` probes are unchanged and stay adapter-side; both verbs stay read-only.

## Migration / Rollout

No migration. Rollback is the proposal's three independent reverts.

## Open Questions

None blocking. The `lint` exit-`2` call remains the orchestrator's, reversible
by reverting the CLI commit alone.
