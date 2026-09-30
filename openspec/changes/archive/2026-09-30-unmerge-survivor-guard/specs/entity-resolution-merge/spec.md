# Delta for Entity Resolution Merge

## ADDED Requirements

### Requirement: Unmerge Refuses When The Survivor Was Edited Since Its Own Merge

`unmerge <survivor-id> <absorbed-id>` MUST compare the survivor's CURRENT
bytes against the bytes the merge itself recorded writing, BEFORE any
preview or confirm prompt, and MUST refuse with no write if they differ.
Every merge binds a `survivor_after_sha256` hash of the exact survivor
bytes it wrote onto the tail `merged_from` ledger entry; `unmerge` MUST
hash the survivor's current text the same way and compare it against that
entry's `survivor_after_sha256` as the FIRST step of Phase A after
resolving the LIFO-tail entry to reverse. This is a distinct check from the
post-confirm drift guard: that one only catches an edit landing during the
confirm prompt's own window, since its baseline is Phase A's own read; this
one catches an edit from ANY point between the merge and the unmerge,
because its baseline is what the merge itself wrote.

The refusal MUST name the survivor and tell the operator to copy the edit
somewhere safe before re-running, MUST NOT suggest a plain re-run as a safe
recovery (a re-run restores the identical stale `survivor_before` snapshot
and discards the edit exactly as the original run would have), and MUST
write nothing. It groups with `unmerge`'s existing pre-prompt Phase A
refusals (the absorbed-path collision, and the link/relation/provenance
drift checks) rather than with the separate post-confirm drift refusal.

The comparison MUST treat ANY later rewrite of the survivor as disqualifying,
regardless of who performed it -- a human hand-edit, or another verb
(`repair`'s status export or migration, `sync-tags`, or the
deprecated-status export the merge itself may write) rewriting the survivor
after the merge committed. This is deliberately conservative: the guard has
no way to distinguish a rewrite that would be safe to discard from one that
would not, so it refuses on all of them alike.

A `merged_from` entry recorded before this requirement existed (schema v1
through the entry shape current at the time of this change) carries no
`survivor_after_sha256` at all. `unmerge` MUST NOT refuse such an entry for
lack of a hash to compare against; instead it MUST print a warning
disclosing that it cannot verify the survivor was unedited since that merge,
and MUST proceed with the restore. This is the one case where the check
fails open, and it MUST be disclosed rather than silent.

An untouched survivor -- whose current bytes still match what the merge
wrote -- MUST be restored exactly as before this requirement: this check
adds no observable change to the documented byte-for-byte round-trip parity
on an otherwise-untouched bundle.
(Previously: `unmerge` restored the survivor from `survivor_before`
unconditionally, with no check of any kind against the survivor's current
bytes; any edit landing between the merge and the unmerge was silently
discarded.)

#### Scenario: Unmerge refuses a survivor edited after the merge

- GIVEN a merge whose survivor was hand-edited afterward, before `unmerge`
  runs
- WHEN `unmerge <survivor> <absorbed>` is run
- THEN it exits non-zero before any preview or prompt, writes nothing, and
  the refusal names the survivor and tells the operator to copy the edit
  somewhere safe before re-running

#### Scenario: Unmerge refuses when another verb rewrote the survivor after the merge

- GIVEN a merge whose survivor was rewritten afterward by a different verb
  (not a human hand-edit), before `unmerge` runs
- WHEN `unmerge <survivor> <absorbed>` is run
- THEN it refuses exactly as it would for a human edit, with no allowance
  for the rewrite's origin

#### Scenario: Unmerge warns but proceeds on a pre-fix ledger entry

- GIVEN a `merged_from` tail entry recorded before this requirement
  existed, carrying no `survivor_after_sha256`
- WHEN `unmerge <survivor> <absorbed>` is run, whether or not the survivor
  was actually edited
- THEN it prints a warning that it cannot verify the survivor was unedited
  since the merge, and proceeds with the restore

#### Scenario: An untouched survivor is unaffected

- GIVEN a merge whose survivor was never touched afterward
- WHEN `unmerge <survivor> <absorbed>` is run
- THEN the survivor-edit check passes silently and the restore proceeds
  exactly as documented by round-trip parity
