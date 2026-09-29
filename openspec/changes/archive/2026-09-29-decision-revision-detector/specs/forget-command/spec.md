# Delta for Forget Command

## MODIFIED Requirements

### Requirement: Deletion Sweep Includes Persisted Findings

`openkos forget` MUST delete, from the live `.openkos/findings.db` store,
every persisted finding whose `pair_ids` (either element) or
`merged_absorbed_id` names a purge-set member. `finding_claims` persists
verbatim claim text quoted from concept bodies, so a finding referencing a
forgotten concept is the same class of leak the ledger and decisions
sweeps already close. The SAME sweep MUST also delete every persisted
ADJUDICATION (issue #779: the `adjudications` tables are the same file's
second tenant) whose member set names a purge-set member -- an
adjudication's `rationale` can quote the member's body verbatim -- under
the same erasure discipline. The SAME sweep MUST ALSO delete, under the
same erasure discipline, every persisted REVISION FINDING (the
`decision-revision-detection` capability's sibling tables, `findings.db`'s
third tenant) whose Decision pair names a purge-set member -- a revision
finding's stored rationale and per-side quotes can embed verbatim text
from either Decision's body. The deletion MUST be an erasure
(no freelist-recoverable pages, no residual WAL images), not a row-level
tombstone. A missing store is a no-op and is never created by the sweep; a
corrupt or unreadable store degrades to a stderr warning naming the
residue and remedy, never an aborted forget.
(Previously: this requirement covered only the `findings` and
`adjudications` tenants of `.openkos/findings.db`. This widens the SAME
sweep, under the same erasure discipline, to also cover
`decision-revision-detection`'s revision-finding tables -- `findings.db`'s
third tenant.)

#### Scenario: Forgetting a concept scrubs its persisted finding claims

- GIVEN `.openkos/findings.db` holds a finding whose `pair_ids` names
  concept id `<id>`, with verbatim `finding_claims` text quoted from its
  body
- WHEN `openkos forget <id>` completes Phase B successfully
- THEN that finding and its claims are deleted, and the quoted claim text
  is not recoverable from the database file's bytes

#### Scenario: An unrelated finding is preserved

- GIVEN a persisted finding references only concepts outside the purge set
- WHEN `openkos forget <id>` completes
- THEN that finding, its claims, and its digest rows are left unchanged

#### Scenario: A corrupt findings store warns instead of aborting

- GIVEN `.openkos/findings.db` exists but cannot be opened as a database
- WHEN `openkos forget <id>` completes its bundle writes
- THEN the forget succeeds and one stderr warning names the possible
  residue and the remedy

#### Scenario: Forgetting a concept scrubs its persisted revision finding

- GIVEN `.openkos/findings.db` holds a revision finding whose Decision pair
  names concept id `<id>`, with a verbatim quote from `<id>`'s body
- WHEN `openkos forget <id>` completes Phase B successfully
- THEN that revision finding is deleted, and the quoted text is not
  recoverable from the database file's bytes

#### Scenario: An unrelated revision finding is preserved

- GIVEN a persisted revision finding that references only a concept outside
  the purge set
- WHEN `openkos forget <id>` completes
- THEN that revision finding is left unchanged
