# Delta for Doctor Command

## MODIFIED Requirements

### Requirement: Doctor Runs And Prints All Applicable Checks

`doctor` MUST execute all checks applicable to the current context —
workspace initialized, `openkos.yaml` valid, Ollama reachable, configured
chat model installed, configured embedding model installed, bundle
readable, workspace vector index present, vector extension loadable, `git`
available, `git-filter-repo` available — and print exactly one
`[PASS]`/`[FAIL]`/`[SKIP]`/`[NOT RUN]` line per applicable check. It MUST
NOT stop or skip remaining checks after any single check fails.

A check whose own read raises an unexpected exception — specifically
`okf.survey_bundle` (the bundle-readable check), `bundle_ledger.scan_torn_writes`
and `bundle_ledger.scan_nesting_violations` (the two merge-ledger checks),
and the injected `reset_point_available()` thunk failing inside the
merge-ledger-integrity check's violation branch — MUST be reported as
`not-run`, a fourth structured outcome carrying the raised reason, and MUST
NOT prevent any other applicable check from running or the report from
rendering. `doctor` MUST print completed and not-run counts
alongside the check lines.
This requirement binds the OBSERVABLE outcome, never a specific exception
class at the service boundary. The `reset_point_available()` case is
deliberately worded as "the thunk fails": `application/` may not import
`openkos.vcs` — the ban is enforced by an AST scan in
`tests/unit/application/test_layering.py` — so the git-specific class stays
adapter-side and the service sees only an adapter-level failure carrying
its reason. A conformance test MUST assert the rendered `[NOT RUN]` line
and the surviving reason, not the identity of the class the service caught.

A check whose own outcome depends on a value the bundle-readable check
produces MUST also report `not-run` when the bundle-readable check reports
`not-run`, rather than guessing at that value: the workspace-vector-index-present
and workspace-fts-present checks each decide between `skip` (an empty
bundle) and `fail` (a non-empty bundle with no index yet) using the
bundle-readable check's own reading of the bundle, and when that reading
did not happen, MUST report `not-run` instead of either — carrying a
reason that names the bundle-readable check as the unmet dependency. Both
downstream checks MUST NOT report `fail` when this applies. This rule does
not extend to a check that can answer without that value: if the
workspace's own index file already exists on disk, the corresponding check
still reports `pass`, since that observation does not depend on the
bundle-readable check at all.

(Previously: these four sites let their exception propagate uncaught,
discarding every already-accumulated `CheckResult` and aborting the report
before it rendered. Previously, a raising bundle-readable check also left
the workspace-vector-index-present and workspace-fts-present checks
reporting `fail` with an `openkos reindex` remediation, because their
`skip`-versus-`fail` decision silently defaulted to treating the unread
bundle as non-empty.)

#### Scenario: Healthy workspace prints all applicable checks

- GIVEN an initialized workspace, valid config, reachable Ollama, both
  configured models installed, a readable bundle, a present workspace vector
  index, a loadable vector extension, and both git binaries available
- WHEN `openkos doctor` runs
- THEN it prints one `[PASS]` line per check, covering all applicable checks

#### Scenario: A failing check does not stop later checks from running

- GIVEN Ollama is unreachable AND `openkos.yaml` is malformed
- WHEN `openkos doctor` runs
- THEN both the config-valid and Ollama-reachable checks print `[FAIL]`,
  and every other applicable check still prints its own result

#### Scenario: A raising bundle-readable check is reported not-run without discarding the rest

- GIVEN an initialized workspace where `okf.survey_bundle` raises an
  unexpected exception
- WHEN `openkos doctor` runs
- THEN the bundle-readable check prints `[NOT RUN]` carrying the raised
  reason, and every other applicable check still prints its own result

#### Scenario: A raising bundle-readable check also stops its two dependent checks from guessing

- GIVEN an initialized workspace where `okf.survey_bundle` raises an
  unexpected exception, and neither the workspace's vector index nor its
  FTS index file exists on disk
- WHEN `openkos doctor` runs
- THEN the bundle-readable, workspace-vector-index-present, and
  workspace-fts-present checks all print `[NOT RUN]` — none of the three
  prints `[FAIL]` — each of the two dependent checks carries a reason
  naming the bundle-readable check, and the report's not-run count
  includes all three

#### Scenario: A merge-ledger scan raise degrades only that check

- GIVEN an initialized workspace where `bundle_ledger.scan_torn_writes` or
  `bundle_ledger.scan_nesting_violations` raises an unexpected exception
- WHEN `openkos doctor` runs
- THEN that merge-ledger check prints `[NOT RUN]` carrying the raised
  reason, and the report still renders every other check's own result

#### Scenario: reset_point_available() raising degrades only the integrity check

- GIVEN a flagged ledger-integrity violation where the underlying git probe
  errors, so the injected `reset_point_available()` thunk fails
- WHEN `openkos doctor` runs
- THEN the merge-ledger-integrity check prints `[NOT RUN]` carrying the
  raised reason, and every other applicable check still prints its own
  result

#### Scenario: The report states completed and not-run counts

- GIVEN a run where one check reports not-run and the rest complete
- WHEN `openkos doctor` runs
- THEN the printed report states how many checks completed and how many did
  not run

### Requirement: Exit Code Reflects Critical Failures Only

`doctor` MUST exit `0` when every applicable check completes (no
`not-run`) and no CRITICAL check (config valid, Ollama reachable, chat
model installed) reports `fail`; `1` when at least one CRITICAL check
reports `fail`, regardless of whether any check also reports `not-run` — a
known critical failure remains the dominant, already-actionable signal; and
`2` when at least one check reports `not-run` and no CRITICAL check reports
`fail` — the report could not be completed. The other seven checks stay
informational: a `fail` on any of them, alone, MUST NOT cause a non-zero
exit, and a `not-run` on any of them, alone (with no critical failure),
MUST NOT push the exit code past `2`.
(Previously: exit was binary — `0`/`1` — driven solely by
`any(status == "fail" and critical)`; no outcome existed for a check whose
read raised, because raising aborted the process before an exit code was
ever chosen.)

#### Scenario: Informational-only failure still exits zero

- GIVEN the vector-extension check fails while every critical check passes
  and no check reports not-run
- WHEN `openkos doctor` runs
- THEN the process exits with code `0`

#### Scenario: Any critical failure causes exit one

- GIVEN one critical check fails while all other checks pass and none
  reports not-run
- WHEN `openkos doctor` runs
- THEN the process exits with code `1`

#### Scenario: Not-run present and every critical check passes exits two, not zero

- GIVEN one check reports not-run (its read raised) and every CRITICAL
  check passes
- WHEN `openkos doctor` runs
- THEN the process exits with code `2`, never `0`

#### Scenario: Not-run present alongside a critical failure still exits one

- GIVEN one check reports not-run AND a CRITICAL check reports `fail`
- WHEN `openkos doctor` runs
- THEN the process exits with code `1`, not `2` — the critical failure
  dominates the incomplete-report signal

#### Scenario: A fully completed healthy run exits zero

- GIVEN every applicable check completes with no not-run outcome and no
  critical failure
- WHEN `openkos doctor` runs
- THEN the process exits with code `0`

## ADDED Requirements

### Requirement: Not-Run Is Structured Data On The Returned CheckResult

`run_diagnostics`'s returned `CheckResult` MUST carry `not-run` as a
`status` outcome that is a peer of `pass`/`fail`/`skip` on the returned
dataclass — not a string invented only when the CLI renders. The raised
reason MUST be part of that same returned value (e.g. its `detail` field),
so any caller of the service, including a non-CLI adapter, can distinguish
`not-run` from `pass`/`fail`/`skip` and read why, without parsing rendered
text.

#### Scenario: The returned value carries not-run before any rendering

- GIVEN a check whose read raises an unexpected exception
- WHEN `run_diagnostics` returns
- THEN the corresponding `CheckResult` in the returned tuple has the
  not-run `status` and carries the raised reason, independent of whether
  the CLI has rendered anything yet

#### Scenario: A non-CLI caller reads not-run without parsing printed text

- GIVEN the same not-run outcome
- WHEN a caller other than the CLI inspects the returned `CheckResult`
  directly
- THEN it distinguishes not-run from `pass`/`fail`/`skip` from the
  structured value alone
