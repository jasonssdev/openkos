# Delta for pending-work

## MODIFIED Requirements

### Requirement: Only Human-Facing Write Paths Resolve A Row

A row MUST move to `applied` only when a human-facing write path --
`curate`, `adjudicate`, `merge`, `relate`, `set-volatility`, `reconcile`,
`suggest-relations --apply`, an ingest that lands a `watch_refusal`'s
bytes, or a decision applied through the curation decision surface (including a
merge-group) -- performs the write the row proposes, and to `declined` only when a
human declines it through a surface that records the decline. A merge-group
performs the write its group's row proposes once its first pairwise merge is
written, and resolves that row `applied` then, as proposed when every member was
marked and modified otherwise, before rows naming the absorbed member are retired.
A run of the job runner MUST NOT resolve a row. The MCP surface MUST NOT change a row's
status. A human-facing path MAY mark a row `claimed` while it presents it,
and a claim MUST lapse back to `pending` when its claimant exits without
resolving it.
(Previously: the paths were named by verb only, and a pairwise merge's retirement of rows naming the absorbed concept left a larger group's row `stale` even when a person had decided that group.)

#### Scenario: A merge resolves its identity row

- GIVEN an open identity row for A and B
- WHEN the operator merges A into B through `openkos merge`
- THEN the row is `applied`

#### Scenario: A merge retires every open row naming the absorbed concept

- GIVEN open rows over A, B and C (an identity row over all three, and
  relation-type rows over A/B and B/C)
- WHEN the operator merges B into A
- THEN every open row that names B is `stale`, so none offers a resolving
  command over a concept that no longer exists
- AND an open row that does not name B is untouched
- AND a row already `applied`, `declined` or `stale` keeps its state

#### Scenario: A merge-group applies its own row, not stale

- GIVEN an open identity row over A, B and C, and a relation-type row over B/C
- WHEN a merge-group marking A, B and C with survivor A is applied through the
  decision surface
- THEN the identity row over A, B and C is `applied` as proposed
- AND the relation-type row over B/C is `stale`

#### Scenario: A different verdict closes the identity row

- GIVEN an open identity row for A and B
- WHEN `openkos adjudicate` (or `curate`) persists a DIFFERENT verdict for
  exactly A and B
- THEN the row is `declined`
- AND no identity decision sidecar is written, so the group is offered again
  if a member is edited or the judgment rubric changes

#### Scenario: A servable different verdict keeps the group out of the queue

- GIVEN a persisted DIFFERENT verdict for A and B that is still servable
- WHEN the identity advisor runs, and when `status` counts identical-title
  groups
- THEN neither offers the group as identity work

#### Scenario: The runner never resolves a row

- GIVEN any open row
- WHEN a maintenance pass completes
- THEN the row is not `applied` or `declined`
