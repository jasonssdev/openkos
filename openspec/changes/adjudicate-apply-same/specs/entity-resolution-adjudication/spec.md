# Delta for entity-resolution-adjudication

## ADDED Requirements

### Requirement: `--apply-same` Eligibility Filter

`adjudicate --apply-same` MUST include a group in the batch ONLY when
`verdict == SAME` AND the group has exactly 2 `member_ids`. DIFFERENT and
UNCERTAIN groups MUST NEVER be included. A SAME group with more than 2
members MUST be skipped (not merged), mirroring `--apply`'s N>2 handling.

#### Scenario: Mixed report yields only SAME 2-member pairs

- GIVEN a report with SAME 2-member, SAME 3-member, DIFFERENT, and UNCERTAIN
  groups
- WHEN `adjudicate --apply-same` runs
- THEN only the SAME 2-member groups appear in the batch

#### Scenario: SAME group with >2 members is skipped

- GIVEN a SAME-verdict group with 3 members
- WHEN `adjudicate --apply-same` runs
- THEN that group is never merged and is reported as skipped

#### Scenario: DIFFERENT/UNCERTAIN groups are skipped

- GIVEN a DIFFERENT-verdict group and an UNCERTAIN-verdict group
- WHEN `adjudicate --apply-same` runs
- THEN neither group is merged

### Requirement: Aggregate Preview Before Any Write

Before any write, `adjudicate --apply-same` MUST print a preview listing
EVERY eligible merge, one per line in the form
`merge <absorbed> into <survivor> ...`, followed by the total eligible
count. No write MUST occur before the confirmation gate is resolved.

#### Scenario: Preview lists all eligible pairs and the count

- GIVEN 3 eligible SAME 2-member groups
- WHEN `adjudicate --apply-same` runs
- THEN stdout lists all 3 pairs before any prompt
- AND stdout shows the total eligible count
- AND no filesystem write has occurred yet

### Requirement: Typed-Count Confirmation Gate

The confirmation gate MUST be resolved in this order:

1. If `--confirm-count <value>` is supplied on the command line, proceed
   ONLY when `value.strip()` exactly equals the eligible-merge count; any
   other value (empty, non-numeric, wrong number) MUST abort with ZERO
   writes.
2. Else, if stdin is a TTY, print the full aggregate preview, then prompt
   the operator to type the exact eligible-merge count; the same
   exact-match-or-abort-zero-writes rule applies.
3. Else (non-TTY and no `--confirm-count`), `adjudicate --apply-same` MUST
   REFUSE with exit code 1 and ZERO writes — it cannot confirm unattended
   without the explicit flag.

Unattended/scripted apply IS possible, but ONLY via an explicit exact
`--confirm-count` match; it is never a silent bypass.

#### Scenario: `--confirm-count <exact>` proceeds

- GIVEN 3 eligible pairs and `--confirm-count 3`
- WHEN `adjudicate --apply-same` runs
- THEN all 3 merges proceed without any interactive prompt

#### Scenario: `--confirm-count <wrong/empty/non-numeric>` aborts with zero writes

- GIVEN 3 eligible pairs and `--confirm-count 2`, `--confirm-count 4`,
  `--confirm-count ""`, or `--confirm-count yes`
- WHEN `adjudicate --apply-same` runs
- THEN the run aborts and zero merges are written

#### Scenario: TTY prompt with exact count typed proceeds

- GIVEN 3 eligible pairs, no `--confirm-count`, a TTY stdin, and typed
  input `"3\n"`
- WHEN `adjudicate --apply-same` runs
- THEN the full aggregate preview is printed, then all 3 merges proceed

#### Scenario: TTY prompt with empty input aborts with zero writes

- GIVEN eligible pairs, no `--confirm-count`, a TTY stdin, and typed input
  `"\n"`
- WHEN `adjudicate --apply-same` runs
- THEN the run aborts, zero merges are written, and the workspace is
  byte-identical to before the run

#### Scenario: TTY prompt with wrong or non-numeric input aborts with zero writes

- GIVEN 3 eligible pairs, no `--confirm-count`, a TTY stdin, and typed
  input `"2\n"`, `"4\n"`, or `"yes\n"`
- WHEN `adjudicate --apply-same` runs
- THEN the run aborts and zero merges are written

#### Scenario: Non-TTY without `--confirm-count` refuses

- GIVEN eligible pairs, no `--confirm-count`, and a non-interactive/non-TTY
  invocation
- WHEN `adjudicate --apply-same` runs
- THEN the run refuses with exit code 1 and zero merges are written

### Requirement: Sequential Execution And Mid-Batch Failure Semantics

On an exact-match confirmation, accepted merges MUST execute sequentially,
reusing the shipped per-pair `prepare_merge`/`merge_core`/`_autocommit`
body, and each MUST land a `merged_from` ledger entry. A mid-batch failure
MUST stop the run but MUST KEEP already-committed merges intact, and the
final report MUST show what was applied versus not attempted.

#### Scenario: Mid-batch failure stops but keeps prior commits

- GIVEN 3 accepted pairs where the 2nd pair fails during `merge_core`
- WHEN `adjudicate --apply-same` runs
- THEN pair 1 remains applied and committed, pair 2 fails with a clear
  error, pair 3 is never attempted, and the run reports what was applied
  versus not

### Requirement: Stale-Id Guard Across Batch

`adjudicate --apply-same` MUST re-verify both member ids of each accepted
pair immediately before applying it. If an earlier merge in the same batch
already absorbed a member that a later pair references, that later pair
MUST be skipped (not crash), and the skip MUST be clearly reported.

#### Scenario: Shared-member pairs are handled without crashing

- GIVEN two eligible SAME pairs sharing one member id
- WHEN `adjudicate --apply-same` applies the first pair
- THEN the second pair is skipped or safely re-resolved, the run does not
  crash, and the skip is clearly reported

### Requirement: Reversibility Via Sequential Unmerge

Every merge applied by `adjudicate --apply-same` MUST be reversible via the
existing `unmerge` command, following the same LIFO per-survivor semantics
as `--apply`. No batch-undo command is provided.

#### Scenario: Batch round-trips via sequential unmerge

- GIVEN a batch of N applied merges
- WHEN `unmerge` is run N times in the correct LIFO order per survivor
- THEN the workspace is restored to byte parity with its pre-batch state

### Requirement: `--apply-same` Mutual Exclusion With `--apply` And `--json`

`adjudicate --apply-same` MUST be mutually exclusive with `--apply` and
with `--json`. Supplying more than one of these flags together MUST be
rejected with a clear stderr message and exit code 2, mirroring the
existing `--apply`/`--json` mutual-exclusion pattern.

#### Scenario: `--apply-same --apply` exits 2

- WHEN `adjudicate --apply-same --apply` runs
- THEN stderr contains a clear rejection message and the exit code is 2

#### Scenario: `--apply-same --json` exits 2

- WHEN `adjudicate --apply-same --json` runs
- THEN stderr contains a clear rejection message and the exit code is 2
