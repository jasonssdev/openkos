# Delta for Doctor Command

Slice 1b (depends on Slice 1a's ledger relocation).

## ADDED Requirements

### Requirement: Merge-Ledger Torn-Write Check

`doctor` MUST add one new check, `merge ledger torn writes`, that scans
`bundle/.state/ledger/` for any `*.ledger.okf.pending` two-phase-write
marker — the trace a `merge`/`unmerge` leaves behind when it crashes
mid-commit. This check is mechanically exact (a marker either exists or it
does not, and its recorded hash either matches the survivor's current
content or it does not), so it has zero false positives and zero false
negatives, unlike the post-merge-mutation check below. It MUST be
informational (its failure alone MUST NOT affect the exit code) and MUST
follow the existing `[PASS]`/`[FAIL]`/`[SKIP]` + `-> <remediation>` shape.
A `[FAIL]` line's remediation MUST name the repair verb, which resolves
the pending marker on the next `merge`/`unmerge` that touches the affected
survivor. This check MUST NOT write, modify, or delete any file — `doctor`
stays read-only; it detects and advises, it never repairs.

#### Scenario: A workspace with no pending markers passes
- GIVEN a workspace where no `merge`/`unmerge` has ever crashed mid-commit
- WHEN `openkos doctor` runs
- THEN the merge-ledger torn-writes check prints `[PASS]`

#### Scenario: A torn write fails with the repair remediation
- GIVEN a `*.ledger.okf.pending` marker under `bundle/.state/ledger/`
- WHEN `openkos doctor` runs
- THEN the check prints `[FAIL]` followed by remediation naming the
  repair verb

#### Scenario: The check never writes
- GIVEN any combination of pending and non-pending ledger sidecars
- WHEN `openkos doctor` runs
- THEN no file under `bundle/.state/ledger/` (or anywhere else) is
  created, modified, or deleted by this check

### Requirement: Merge-Ledger Integrity Check

`doctor` MUST add one new check, `merge ledger entries free of post-merge
mutation`, that inspects every sidecar under `bundle/.state/ledger/` and
flags an entry whose recorded snapshot(s) no longer match what a
byte-exact `unmerge` would require — i.e. the ledger was mutated by
something other than the merge/unmerge machinery after being written. This
check MUST be informational (its failure alone MUST NOT affect the exit
code) and MUST follow the existing `[PASS]`/`[FAIL]`/`[SKIP]` +
`-> <remediation>` shape used by every other check. A `[FAIL]` line's
remediation MUST name BOTH the repair verb (for a ledger that is merely
unmigrated, not corrupted) and `git reset --hard <first-merge>~1` followed
by `openkos reindex` (for a ledger the check judges corrupted), and MUST
state that reversibility of merges made before this fix is not guaranteed.
This check MUST NOT write, modify, or delete any file — `doctor` stays
read-only; it detects and advises, it never repairs.

#### Scenario: Clean ledgers pass
- GIVEN a workspace whose every `bundle/.state/ledger/` sidecar matches
  its expected byte-exact-restore state
- WHEN `openkos doctor` runs
- THEN the merge-ledger-integrity check prints `[PASS]`

#### Scenario: A corrupted ledger fails with both remediation paths
- GIVEN a `bundle/.state/ledger/` sidecar whose recorded snapshots no
  longer round-trip byte-exact
- WHEN `openkos doctor` runs
- THEN the check prints `[FAIL]` followed by remediation naming both the
  repair verb and the `git reset --hard`+`openkos reindex` path, and
  stating pre-fix reversibility is not guaranteed

#### Scenario: The check never writes
- GIVEN any combination of clean and corrupted ledgers
- WHEN `openkos doctor` runs
- THEN no file under `bundle/.state/ledger/` (or anywhere else) is
  created, modified, or deleted by this check

#### Scenario: A workspace with no ledger sidecars passes trivially
- GIVEN a workspace where no merge has ever occurred
- WHEN `openkos doctor` runs
- THEN the merge-ledger-integrity check prints `[PASS]`, having found no
  sidecar to flag
