# Delta for Lint

## MODIFIED Requirements

### Requirement: Non-NFC Names Scan

`openkos lint` MUST flag every on-disk name (file OR directory) under
`bundle_dir` that is not NFC (Unicode Normalization Form C) as a
`non-nfc-name` finding, one finding per decomposed directory covering its
entire subtree rather than one per descendant. This scan MUST remain
read-only: it MUST NOT write, rename, or delete any bundle file, and a
`non-nfc-name` finding's presence alone MUST NOT gate the command's exit
code (informational only, per the Non-Gating Exit Contract). The finding's
detail MUST name both the raw on-disk spelling and the NFC target spelling,
and MUST remediate by naming `openkos normalize-names` as the command that
performs the rename — it MUST NOT assert that openkos never renames.

WHEN this scan's own directory walk raises an unreadable-directory error
(`OSError`), the check MUST degrade to `not-run` — carrying the raised
reason — rather than raising uncaught, and MUST NOT discard any of the
other already-computed finding lists. A `not-run` outcome here IS gating on
run-completeness, per the Non-Gating Exit Contract's incomplete-run
vocabulary — distinct from an ordinary finding, which stays non-gating.
(Merged from change `nfc-rename-migration`, PR #492; first formal capture
of the detection shipped in #490.)
(Previously: an `OSError` from this scan's walk propagated uncaught,
discarding all 11 other already-computed finding lists and aborting the
report before it rendered.)

#### Scenario: A decomposed on-disk name is flagged

- GIVEN an on-disk file or directory name under `bundle_dir` that is not
  NFC
- WHEN `openkos lint` runs
- THEN it reports a `non-nfc-name` finding naming the raw spelling and the
  NFC target spelling

#### Scenario: A decomposed directory produces one finding for its whole subtree

- GIVEN a decomposed directory containing offending descendant entries
- WHEN `openkos lint` runs
- THEN it reports exactly one `non-nfc-name` finding for the directory,
  not one per descendant

#### Scenario: The remediation names normalize-names, not "openkos never renames"

- GIVEN a `non-nfc-name` finding
- WHEN its detail text is inspected
- THEN it names `openkos normalize-names` as the command that performs
  the rename, and it does not assert that openkos never renames

#### Scenario: The scan stays read-only and its findings stay non-gating

- GIVEN a bundle containing one or more `non-nfc-name` findings and no
  not-run outcome
- WHEN `openkos lint` runs
- THEN it reports the findings, still exits `0`, and no bundle file is
  created, modified, renamed, or deleted

#### Scenario: An unreadable directory during the scan degrades to not-run without losing other findings

- GIVEN the non-NFC-names walk hits a directory it cannot read (`OSError`),
  and the other 11 finding lists have already been computed
- WHEN `openkos lint` runs
- THEN the non-nfc-name check reports `not-run` with the raised reason, and
  every other already-computed finding list still renders

### Requirement: Read-Only and Human-Readable Only

`openkos lint` MUST NOT write, modify, or delete any bundle file, and MUST
produce human-readable text output only; no `--json` or other structured
output mode is offered. Findings MUST be flat warning-level (no
error/warning tiers).

Two further late bundle walks fall under this same read-only guarantee and
share the Non-NFC scan's not-run failure-mode contract:
`check_state_dir_contains_no_markdown` and `check_dot_dir_markdown`. WHEN
either walk's own directory read raises an `OSError`, that check MUST
degrade to `not-run` — carrying the raised reason — rather than raising
uncaught, and MUST NOT discard any of the other already-computed finding
lists.
(Previously: an `OSError` from either walk propagated uncaught, discarding
all other already-computed finding lists and aborting the report before it
rendered; neither walk's failure mode was specified at all.)

#### Scenario: No mutation on any run

- GIVEN any workspace state (empty, clean, or with findings)
- WHEN `openkos lint` runs
- THEN no file under the workspace is created, modified, or deleted, and
  no `--json` flag is accepted

#### Scenario: An unreadable directory during the dot-dir walk degrades only that check

- GIVEN `check_dot_dir_markdown`'s walk raises `OSError` on an unreadable
  directory
- WHEN `openkos lint` runs
- THEN that check reports `not-run` with the raised reason, and every
  other finding list still renders

#### Scenario: An unreadable directory during the state-dir walk degrades only that check

- GIVEN `check_state_dir_contains_no_markdown`'s walk raises `OSError` on
  an unreadable directory
- WHEN `openkos lint` runs
- THEN that check reports `not-run` with the raised reason, and every
  other finding list still renders

### Requirement: Non-Gating Exit Contract

`openkos lint` MUST exit `0` on any complete run — clean or with findings
— where no check reports `not-run`. It MUST exit `2` when at least one late
bundle walk (Non-NFC names, dot-dir markdown, state-dir markdown) reports
`not-run`, i.e. the report is incomplete, even when every other check
completed and produced only informational findings or none at all.
Findings themselves stay non-gating: a `2` exit is caused only by an
incomplete run, never by a finding's presence. `lint` MUST NOT be a CI gate
in MVP-1 on findings; the only nonzero exits are the pre-existing
"workspace cannot be read" case and this incomplete-run case. `lint` MUST
print completed and not-run counts alongside its findings sections.
(Previously: the only nonzero exit was "workspace cannot be read"; no
vocabulary distinguished an incomplete run from a clean or found-something
run, and a failing late walk crashed uncaught before reaching this rule at
all.)

#### Scenario: Empty or fresh bundle has no findings

- GIVEN an initialized workspace with no stale stamps or orphan concepts,
  and no not-run outcome
- WHEN `openkos lint` runs
- THEN it reports a sensible empty-state message with no findings and
  exits `0`

#### Scenario: Bundle with findings still exits 0

- GIVEN a bundle containing at least one stale-stamp or orphan-page
  finding and no not-run outcome
- WHEN `openkos lint` runs
- THEN it reports the findings and exits `0`

#### Scenario: A not-run late walk exits two even with no findings

- GIVEN one late walk reports not-run and no other finding exists
- WHEN `openkos lint` runs
- THEN it exits `2`, not `0`

#### Scenario: A not-run late walk exits two regardless of other findings present

- GIVEN one late walk reports not-run and other checks report ordinary
  findings
- WHEN `openkos lint` runs
- THEN it still exits `2` — findings alone would exit `0`, but the
  incomplete run overrides that

#### Scenario: The report states completed and not-run counts

- GIVEN a run where one late walk reports not-run and the rest complete
- WHEN `openkos lint` runs
- THEN the printed report states how many checks completed and how many
  did not run

## ADDED Requirements

### Requirement: Not-Run Is Structured Data On The Returned LintReport

`build_lint_report`'s returned `LintReport` MUST carry a `not_run` field —
naming which late-walk check(s) could not run and their reasons — as a
peer of its existing finding-list fields, not a string invented only when
the CLI renders. Any caller of the service, including a non-CLI adapter,
MUST be able to read which checks did not run, and why, from the returned
`LintReport` alone. `LintReport`'s other eleven fields MUST remain
populated with whatever findings were gathered before the failing walk,
even when `not_run` is non-empty.

#### Scenario: A failing late walk leaves prior findings intact and records itself in not_run

- GIVEN `check_dot_dir_markdown` raises `OSError` while the other ten
  checks complete
- WHEN `build_lint_report` returns
- THEN the returned `LintReport.not_run` names that check and its reason,
  while its other ten already-computed finding-list fields retain their
  gathered findings

#### Scenario: A fully complete run reports an empty not_run

- GIVEN a run where every late walk completes without raising
- WHEN `build_lint_report` returns
- THEN the returned `LintReport.not_run` is empty
