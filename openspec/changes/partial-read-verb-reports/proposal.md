# Proposal: partial-read-verb-reports — a read verb reports what it could not do

## Intent

Refs #1002, items **A** and **C**. `doctor` and `lint` are compute-then-render:
one raise inside the service destroys the whole report. `doctor` loses all 15
`CheckResult`s when `survey_bundle` or either ledger scan raises
(`application/doctor.py:453, 665, 709, 711`); `lint` loses 11 computed finding
lists when any of three unguarded late bundle walks raises
(`application/lint.py:168, 171, 178`). Both gaps are **pre-existing** — verified
against `git show main:src/openkos/cli/main.py`, not regressions from the
read-core extraction chain.

## Scope

### In Scope

1. `doctor`: each of the four raise sites becomes a per-check outcome; the other
   checks still render.
2. `lint`: each of the three late walks is wrapped individually; the 11 other
   finding lists still render.
3. "Did not run" as a **third structured state on the value the service returns**,
   a peer of pass/fail — not a string the renderer invents.
4. Exit-code vocabulary for both verbs: `0` healthy, `1` a critical check failed,
   `2` the report could not be completed.
5. Completed / not-run counts printed by both verbs.
6. The one in-workspace `doctor` test that A's behaviour requires (a genuine
   counterpart to `test_doctor_service.py:117`, which runs against `tmp_path` and
   therefore never reaches checks 6/12/13).

### Out of Scope — deliberate, not an oversight

#1002 items **B, D, E, F** land as separate follow-ups. Only A and C are
authorized here. In particular **E** (test-coverage debt) is taken only to the
extent item 6 above requires; it is not adopted wholesale.

## Decisions

| Decision | Accepted | Rejected |
| --- | --- | --- |
| Failure handling | render the partial report, mark each check that could not run | abort the report (today's behaviour) |
| Where "did not run" lives | structured state on the service return value — *"an MCP adapter needs that distinction as data"* (#1002); MVP 3's MCP server consumes these same services | a renderer-invented string |
| `doctor` exit codes | `0`/`1`/**`2` = diagnosis incomplete** — NEW public surface | fold into `1`; leave the exit rule untouched |
| `lint` exit codes | same vocabulary; `2` = incomplete | split the vocabulary per verb |
| `lint` result shape | option 1's blast radius carrying option 3's vocabulary | envelope all 14 fields; a lint-only private vocabulary |

**Why exit 2 is required.** Today a raise makes the process exit nonzero. A fix
that left the exit rule untouched would newly exit **0** on a workspace broken
enough that three diagnostics never ran — trading an ugly traceback for a false
"healthy". That is the single biggest risk in this change, and `2` exists to
prevent it. The predicate at `cli/main.py:15041` is
`any(r.status == "fail" and r.critical ...)`, which a new status value does not
match, so the rule must be rewritten rather than inherited.

**`lint`'s exit `2` is the orchestrator's call, not the user's, and is open to
reversal.** `lint`'s "Non-Gating Exit Contract" already exits nonzero when the
workspace cannot be read, and a failed late walk *is* that case — so this refines
*how* the nonzero arises, not *whether*.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `doctor-command`: "Doctor Runs And Prints All Applicable Checks" (a check that
  cannot run is reported, not fatal) and "Exit Code Reflects Critical Failures
  Only" (gains exit `2`).
- `lint`: "Non-NFC Names Scan" plus the dot-dir and state-dir checks under
  "Read-Only and Human-Readable Only" (each degrades to not-run), and "Non-Gating
  Exit Contract" (names exit `2`).

## Approach

One shared primitive under `application/` names the not-run fact and its reason.
`doctor` extends `CheckResult.status`'s `Literal` with that token; `lint` gains
`LintReport.not_run: tuple[...]`, leaving its other 11 fields untouched. This is
the explorer's option 1 shape carrying option 3's vocabulary: an MCP adapter reads
one word across both verbs, and no render site for an existing field changes.
Option 2 (envelope every field) is rejected — it touches all 14 fields to express
a fact that applies to three.

`application/status.py`'s `StatusOverview`/`StatusReport` split does **not**
transfer: it answers interleaved print-and-read, which neither module has. The
fix belongs inside the single call, per check.

**ADR verdict: one ADR, written during design.** It records *"an incomplete read
verb reports incompleteness as data and as exit code `2`"*. Both gate conditions
hold: it decides a public CLI interface plus a cross-service vocabulary, and it is
hard to reverse — once released, user scripts and the MVP 3 MCP adapter read `2`
and the not-run token, so withdrawing either is a breaking change. The per-check
`try`/`except` alone would not have earned one.

## Affected Areas

| Area | Impact | Description |
| --- | --- | --- |
| `src/openkos/application/` | New | one leaf primitive for the not-run outcome |
| `src/openkos/application/doctor.py` | Modified | 4 raise sites become outcomes; `status` `Literal` widens |
| `src/openkos/application/lint.py` | Modified | 3 late walks wrapped individually |
| `src/openkos/lint.py` | Modified | `LintReport` gains `not_run` |
| `src/openkos/cli/main.py` | Modified | renders not-run lines and counts; both exit rules rewritten |
| `tests/unit/cli/test_lint.py` | Modified | `test_lint_lets_a_late_name_walk_failure_propagate_uncaught` (line 1113) pins today's crash |
| `tests/unit/application/test_doctor_service.py` | Modified | in-workspace counterpart for checks 6/12/13 |

## Principles Impact

Both verbs stay **read-only** — nothing here writes. `raw/` immutability,
reconstructibility, OKF conformance, provenance and sensitivity are untouched:
no canonical file, format surface, or ingestion path changes. **Human curates,
engine maintains** is strengthened — an incomplete diagnosis is now stated rather
than replaced by a traceback or by silence. Layering holds: the new primitive is a
leaf under `application/`, imports no backend (ADR-0018 D1), adds no
canonical→derived edge, introduces no `engine.py`, and stays synchronous.

## Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| A "safer" partial report quietly weakens `doctor`'s exit signal | **High** | Exit `2` exists for this; a spec scenario MUST pin *not-run + all criticals pass → exit 2*, and it must be shown to fail before the exit rule is rewritten |
| The rewritten `test_lint` assertions are assumed rather than derived | Med | Design states the replacement assertions explicitly; do not assume they simply flip from raise to non-raise |
| Exit `2` breaks a caller treating any nonzero as failure | Low | Callers already saw nonzero here (a raise); `2` narrows meaning rather than adding a new failure class |
| Scope creep into #1002 B/D/E/F | Med | Out-of-scope stated above; item 6 is the only E-adjacent work |

## Rollback Plan

Three independent reverts, in this order: (1) the CLI exit-rule commit — both
verbs return to today's predicate and exit `2` disappears from the public surface;
(2) the `lint` wrapping commit — `LintReport.not_run` is dropped and the late
walks raise again; (3) the `doctor` wrapping commit — the `Literal` narrows and
the four sites raise again. Reverting (1) alone removes the risky public surface
while keeping the partial reports, which is the expected partial rollback. No data
migration: nothing touches `raw/`, any canonical file on disk, or any stored
state. Tests added by items 1, 2 and 6 are kept — they document the gap either way.

## Dependencies

None. The read-core extraction chain (#999, #1000, #1001, #1003) has merged.

## Rider — reviewer's call, not adopted here

`doctor-command/spec.md`'s "Doctor Prints A Leading Version Banner" says doctor
emits **ten** `CheckResult`s (lines 353, 367) while the tested count is **fifteen**
(`test_run_diagnostics_returns_exactly_fifteen_checks`). Pre-existing drift,
unrelated to A/C. This change can correct it in one line; the reviewer decides
whether it rides along or gets its own change. Scope is not expanded silently.

## Size

Estimated ~250-400 changed lines across source and tests — near the 400-line
review budget. If the forecast at `sdd-tasks` exceeds it, slice as `doctor` first,
then `lint`, then the shared exit vocabulary.

## Success Criteria

- [ ] Each of the 7 raise sites (4 in `doctor`, 3 in `lint`) has a test that fails
      before the fix and passes after, asserting the surviving report content.
- [ ] `doctor` renders the other 14 checks when check 6, 12 or 13 cannot run.
- [ ] `lint` renders its other 11 finding lists when any late walk fails.
- [ ] Both verbs print completed / not-run counts.
- [ ] `doctor` exits `2` when a check could not run and no critical check failed;
      `1` still means a critical check failed.
- [ ] `lint` exits `2` on an incomplete run and `0` on any complete run.
- [ ] The not-run state is readable from the service return value without parsing
      rendered text.
- [ ] `uv run pre-commit run --all-files` and `uv run pytest --cov` green (90%
      branch gate).
- [ ] The ADR exists with status `Proposed`, and #1002 records that A and C shipped
      while B, D, E, F remain open.
