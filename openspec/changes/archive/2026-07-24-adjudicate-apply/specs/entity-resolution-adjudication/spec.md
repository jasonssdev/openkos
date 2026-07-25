# Delta for Entity-Resolution Adjudication

## ADDED Requirements

### Requirement: `--apply` Eligibility Filter

`adjudicate --apply` MUST offer a group for interactive merge ONLY when
`verdict == SAME` AND the group has exactly 2 `member_ids`. DIFFERENT and
UNCERTAIN groups MUST NEVER be offered. A SAME group with more than 2 members
MUST NOT be prompted; it MUST print `skipped (N>2, merge manually)` for that
group, where `N` is its member count.

#### Scenario: SAME 2-member group is offered

- GIVEN a SAME-verdict group with exactly 2 members
- WHEN `adjudicate --apply` runs
- THEN that group is prompted for merge

#### Scenario: DIFFERENT group is never offered

- GIVEN a DIFFERENT-verdict group
- WHEN `adjudicate --apply` runs
- THEN that group is never prompted

#### Scenario: SAME group with >2 members is skipped, not prompted

- GIVEN a SAME-verdict group with 3 members
- WHEN `adjudicate --apply` runs
- THEN stdout shows `skipped (N>2, merge manually)` for that group
- AND the group is never prompted

### Requirement: Survivor/Absorbed Preview And Prompt

For each eligible group, survivor MUST be `member_ids[0]` (alphabetical-first)
and absorbed MUST be `member_ids[1]`. Before prompting, a preview of what
`prepare_merge` would fuse (survivor, absorbed, rewrites, removed) MUST be
printed. The prompt text MUST be exactly
`Merge <absorbed> into <survivor>? [y/N/skip]`.

#### Scenario: Preview precedes the exact prompt text

- GIVEN an eligible SAME 2-member group
- WHEN `adjudicate --apply` runs
- THEN a `prepare_merge` preview is printed before the prompt
- AND the prompt line is exactly `Merge <absorbed> into <survivor>? [y/N/skip]`
  with `<survivor>` = `member_ids[0]` and `<absorbed>` = `member_ids[1]`

### Requirement: Prompt Response Semantics

Only an input of `y` or `Y` MUST result in the merge being applied. Empty
input, `skip`, and `N`/`n` MUST NOT apply the merge and MUST continue to the
next group.

#### Scenario: `y` applies the merge

- GIVEN an eligible group and CliRunner `input="y\n"`
- WHEN `adjudicate --apply` runs
- THEN the merge is applied

#### Scenario: empty input does not merge

- GIVEN an eligible group and CliRunner `input="\n"`
- WHEN `adjudicate --apply` runs
- THEN the merge is NOT applied and the run continues

#### Scenario: `skip` does not merge

- GIVEN an eligible group and CliRunner `input="skip\n"`
- WHEN `adjudicate --apply` runs
- THEN the merge is NOT applied and the run continues

#### Scenario: `N`/`n` does not merge

- GIVEN an eligible group and CliRunner `input="n\n"`
- WHEN `adjudicate --apply` runs
- THEN the merge is NOT applied and the run continues

### Requirement: Accepted Merge Executes And Is Reversible

On `y`, `adjudicate --apply` MUST execute the merge via `merge_core`: the
survivor file is updated, the absorbed file is removed, index/log are
updated, and a `merged_from` ledger entry is written. The result MUST be
reversible via `unmerge`.

#### Scenario: Applied merge updates filesystem and ledger

- GIVEN an eligible group with `input="y\n"`
- WHEN `adjudicate --apply` runs
- THEN the survivor file is updated, the absorbed file no longer exists, and
  a `merged_from` ledger entry references the absorbed id

#### Scenario: Applied merge is unmerge-reversible

- GIVEN an applied merge from `adjudicate --apply`
- WHEN `unmerge` is run against the survivor
- THEN the absorbed member is restored

### Requirement: Per-Merge Auto-Commit

Each applied merge MUST be auto-committed independently — one commit per
merge, not one commit for the whole run.

#### Scenario: Two applied merges produce two commits

- GIVEN two eligible groups both answered `y`
- WHEN `adjudicate --apply` runs
- THEN two separate commits are created, one per applied merge

### Requirement: Stale-Id Guard Across Sequential Merges

Before acting on an eligible group, `adjudicate --apply` MUST re-verify both
member ids still exist. If an earlier accepted merge in the same run
absorbed a member that a later group references, that later group MUST print
`skipped (member already merged)` and MUST NOT be prompted or crash.

#### Scenario: Later group referencing an already-absorbed member is skipped

- GIVEN two SAME 2-member groups sharing one member id, the first merge
  accepted with `y`
- WHEN `adjudicate --apply` continues to the second group
- THEN stdout shows `skipped (member already merged)` for that group
- AND the run does not crash

### Requirement: `--apply` Rejects `--json`

`adjudicate --apply --json` MUST be rejected with a clear stderr message and
exit code 2, since interactive and machine-readable modes are contradictory.

#### Scenario: `--apply --json` exits 2

- WHEN `adjudicate --apply --json` runs
- THEN stderr contains a clear rejection message
- AND the exit code is 2

### Requirement: `--apply` Composes With `--same-only` As A No-Op

`adjudicate --apply --same-only` MUST behave identically to
`adjudicate --apply` alone, since `--apply` is inherently SAME-only.

#### Scenario: `--apply --same-only` behaves like `--apply`

- GIVEN the same fixture and inputs
- WHEN `adjudicate --apply` and `adjudicate --apply --same-only` each run
- THEN both produce the same eligibility set, prompts, and outcomes

### Requirement: Mid-Run Write Failure Stops The Run

If `merge_core` fails for an accepted merge, `adjudicate --apply` MUST stop
immediately with a clear error message and MUST NOT silently continue to
remaining groups. Commits from prior successfully applied merges in the same
run MUST remain intact and reversible.

#### Scenario: `merge_core` failure halts remaining groups

- GIVEN two eligible groups, the first accepted merge fails inside
  `merge_core`
- WHEN `adjudicate --apply` runs
- THEN a clear error message is shown, the run stops before the second
  group, and the exit code is non-zero

### Requirement: End-Of-Run Summary With Breakdown

At the end of the run, `adjudicate --apply` MUST print a summary line
`applied X, skipped Y` where `Y` breaks down into N>2 skips, already-merged
skips, and declined (N/skip/empty) prompts.

#### Scenario: Summary reflects applied and skipped counts

- GIVEN a run with one applied merge, one N>2 skip, and one declined prompt
- WHEN `adjudicate --apply` completes
- THEN stdout shows `applied 1, skipped 2` with the breakdown of skip reasons

### Requirement: Empty / No-Eligible State

WHEN no SAME 2-member groups exist, `adjudicate --apply` MUST print a clear
message, apply nothing, and exit 0.

#### Scenario: No eligible groups, nothing applied

- GIVEN a bundle whose results contain no SAME 2-member groups
- WHEN `adjudicate --apply` runs
- THEN stdout shows a clear "nothing to apply" message
- AND no merge is performed
- AND the exit code is 0

### Requirement: Plain `adjudicate` Is Unchanged

`adjudicate` without `--apply` — plain, with `--json`, or with `--same-only`
— MUST behave exactly as before this change; no output, exit code, or
filesystem behavior on these paths may regress.

#### Scenario: Non-`--apply` behavior is unaffected

- GIVEN any pre-existing CliRunner assertion on `adjudicate`, `adjudicate
  --json`, or `adjudicate --same-only`
- WHEN that command runs after this change
- THEN the assertion still passes unchanged
