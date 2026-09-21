# Exploration: partial-read-verb-reports

Refs #1002 (items A and C). Findings below were produced by the explore phase and
spot-checked against the code by the orchestrator. Persisted here because the
explorer's Engram save failed on an ambiguous multi-session conflict.

## A — `doctor` loses every accumulated check on one raise

`src/openkos/application/doctor.py::run_diagnostics` (line 173) is
**compute-then-render**: it returns one `tuple[CheckResult, ...]` of 15 checks to
`cli/main.py::doctor()` (line 14900), and `_render_check` (line 14876) prints only
after the whole call has returned. `CheckResult` (`doctor.py:139`) is a frozen
dataclass: `label: str`, `status: Literal["pass", "fail", "skip"]`,
`critical: bool`, `remediation`, `detail`.

Four sites raise straight out of `run_diagnostics`, discarding all 15 results:

| Site | Check | Location |
| --- | --- | --- |
| `okf.survey_bundle(...)` | 6 | `doctor.py:453` |
| `bundle_ledger.scan_torn_writes(...)` | 12 | `doctor.py:665` |
| `bundle_ledger.scan_nesting_violations(...)` | 13 | `doctor.py:709` |
| injected `reset_point_available()` thunk | 13, only inside `if violations:` | `doctor.py:711` |

The CLI's `_reset_point_available` (~line 14995) deliberately lets
`vcs_git.GitError` propagate.

The exit predicate is `any(r.status == "fail" and r.critical for r in results)` at
`cli/main.py:15041`. A new status value does **not** match `status == "fail"`,
which is why the exit rule must be decided explicitly rather than inherited.

## C — `lint` discards 11 computed finding lists on a late walk failure

`src/openkos/application/lint.py::build_lint_report` (line 103) returns
`lint.LintReport` (`src/openkos/lint.py:202`), a flat dataclass of 14
`list[LintFinding]` fields plus `notices`.

Only the three INPUT reads are guarded into `LintInputUnavailable`
(`application/lint.py:93`), caught at `cli/main.py:10797`. Three late checks walk
the bundle themselves and are unguarded:

| Check | Defined | Called from `build_lint_report` |
| --- | --- | --- |
| `check_non_nfc_names` | `lint.py:1744` | line 168 |
| `check_state_dir_contains_no_markdown` | `lint.py:1600` | line 171 |
| `check_dot_dir_markdown` | `lint.py:1668` | line 178 |

An `OSError` from any of them discards 11 already-computed finding lists.

## Both gaps are pre-existing

Verified against `git show main:src/openkos/cli/main.py`. Neither gap was
introduced by the read-core extraction chain (#999, #1000, #1001, #1003).

## Result-shape options considered for `lint`

`doctor`'s shape is settled — extend the `Literal`. For `lint` the explorer found
three shapes and flagged the choice as the real fork:

1. Add `LintReport.not_run: tuple[<new frozen type>, ...]` naming which late-walk
   kinds failed and why; wrap each of the three calls individually; the three
   existing fields stay empty on failure. Lowest blast radius — 11 of 14 fields
   untouched, and `LintReport`'s only consumer is `cli/main.py::lint()`.
2. Wrap every field in a `CheckOutcome[...]` envelope. Rejected up front: touches
   all 14 fields and every render site.
3. Define one shared `application/` primitive (e.g. `ReadOutcome`) used by BOTH
   `doctor`'s status and `lint`'s new field, so an MCP adapter reads one
   vocabulary across both verbs. Higher coupling and design cost, but the more
   principled answer to "the distinction is data".

## No transferable prior art

`application/status.py`'s `StatusOverview`/`StatusReport` split does **not**
transfer: it exists because `status`'s pre-extraction body interleaved printing
with reads. Both `doctor.py` and `lint.py` are compute-then-render with no
interleaving, so the fix must live inside the single call, per check.
`lifecycle.py::PartialForgetWrite` is a write-side K-of-N pattern, also not
reusable.

## Blast radius

- `tests/unit/cli/test_lint.py::test_lint_lets_a_late_name_walk_failure_propagate_uncaught`
  (line 1113) asserts `result.exit_code != 0` and
  `isinstance(result.exception, OSError)`. It pins today's crash and must be
  rewritten; design must confirm the replacement assertions rather than assume
  they simply flip.
- `tests/unit/application/test_doctor_service.py::test_run_diagnostics_never_raises_for_any_injected_failure_mode`
  (line 117) runs against `tmp_path`, which is NOT a workspace, so checks 6/12/13
  skip by construction and the test never exercises A's raise paths despite its
  name. It needs a genuine in-workspace counterpart.
- `tests/unit/cli/test_doctor.py` has no test pinning the checks-6/12/13 crash, so
  A's test blast radius is smaller than C's.

## Spec surface

- `openspec/specs/doctor-command/spec.md` — MODIFIED: "Doctor Runs And Prints All
  Applicable Checks", "Exit Code Reflects Critical Failures Only".
- `openspec/specs/lint/spec.md` — MODIFIED: "Non-NFC Names Scan", the dot-dir and
  state-dir checks under the "Read-Only and Human-Readable Only" umbrella, and
  "Non-Gating Exit Contract".
- `openspec/specs/list-command/spec.md` has zero `--sources` mentions, so
  out-of-scope item D has no colliding spec surface.

### Pre-existing drift (rider, not scope)

`doctor-command/spec.md`'s "Doctor Prints A Leading Version Banner" says doctor
emits **ten** `CheckResult`s (lines 353 and 367) while the tested count is
**fifteen** (`test_run_diagnostics_returns_exactly_fifteen_checks`). Unrelated to
A/C; recorded so the reviewer can decide whether this change fixes it in one line.
