# Delta for Sensitivity-Aware LLM

Slice 1a.

## ADDED Requirements

### Requirement: Per-Entry Merged-Content Gate, Never Per-Survivor

`sensitivity.merged_content_blocked` MUST be invoked ONCE PER LEDGER ENTRY
read from a survivor's `bundle/.state/ledger/` sidecar, ranking fail-closed
over `current_sensitivity`, `entry.sensitivity_before`, and
`entry.sensitivity_after` for that entry alone. It MUST NOT be invoked once
per survivor across the whole sidecar: a survivor whose current sensitivity
was lowered via `set-sensitivity` (ADR-0008) after absorbing entries
written at a higher sensitivity MUST still block those specific entries
individually, even when other entries in the same sidecar are not blocked.

#### Scenario: One high-sensitivity entry blocks while a sibling entry in the same sidecar does not
- GIVEN a survivor's sidecar with two entries, one whose
  `sensitivity_before`/`sensitivity_after` exceed the survivor's current
  (lowered) sensitivity and one that does not
- WHEN merged-body candidates are evaluated for that survivor
- THEN `merged_content_blocked` is called once for each of the two entries
  and returns different outcomes for them

#### Scenario: A call hoisted to per-survivor is detected as wrong
- GIVEN a survivor sidecar with 3 entries, only 1 of which should block
- WHEN the gate is invoked exactly once for the whole survivor instead of
  once per entry
- THEN the test asserting per-entry invocation count fails, distinguishing
  a per-survivor implementation from the required per-entry one

## MODIFIED Requirements

### Requirement: Walk-Incompleteness Observability

The system MUST detect when the directory walk underlying the fail-closed
sensitivity filter is provably incomplete (`okf._walk_errors` reports one or
more unlistable subdirectories) and MUST emit a warning to STDERR identifying
the incomplete-walk condition, for each of the five sensitivity-filter verbs:
`query`, `contradictions`, `adjudicate`, `suggest-relations`,
`suggest-volatility`. This detection MUST cover BOTH the concept walk under
`bundle/**.md` AND the ledger-sidecar walk under `bundle/.state/`: an
unlistable subdirectory in either location MUST trigger the warning. The
command MUST still exit 0 (WARN, not refuse). The warning MUST be skipped
when `--include-confidential` is passed, since the filter is then
deliberately disabled.
(Previously: the walk-incompleteness check covered only `bundle/**.md`;
`bundle/.state/` did not exist as a scanned location.)

#### Scenario: Incomplete concept walk warns and still exits 0
- GIVEN a bundle where `okf._walk_errors` reports at least one unlistable
  subdirectory under `bundle/**.md`
- WHEN `query`, `contradictions`, `adjudicate`, `suggest-relations`, or
  `suggest-volatility` runs without `--include-confidential`
- THEN the command prints a warning to STDERR identifying the incomplete
  walk and exits 0

#### Scenario: Incomplete ledger-sidecar walk also warns
- GIVEN a bundle where `bundle/.state/` contains an unlistable
  subdirectory, with the concept walk otherwise clean
- WHEN any of the five verbs runs without `--include-confidential`
- THEN the command prints a warning to STDERR identifying the incomplete
  walk and exits 0

#### Scenario: Clean bundle produces no warning
- GIVEN a bundle where `okf._walk_errors` reports no unlistable
  subdirectories anywhere, including `bundle/.state/`
- WHEN any of the five verbs runs
- THEN no incomplete-walk warning is printed to STDERR

#### Scenario: `--include-confidential` suppresses the warning
- GIVEN a bundle where either walk reports an unlistable subdirectory
- WHEN any of the five verbs runs WITH `--include-confidential`
- THEN no incomplete-walk warning is printed, since the filter is
  deliberately off
