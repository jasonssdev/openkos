# Pending Work Specification

## Purpose

Durable persistence for machine-computed findings and operator decisions
produced by `curate`'s advisor stages. This slice covers the Contradictions
advisor only (#556): a `CONTRADICTS`/`CONSISTENT`/`UNCERTAIN` verdict from
`find_contradictions`, and an operator's decline of one.

**Out of scope.** The other three advisor kinds — candidate edges, duplicate
groups, volatility proposals — and issues #553 (FTS never built) and #557
(false all-clear on an unrelated graph) are not governed by this spec.

Findings and decisions have opposite natures and are stored separately.
Findings are recomputable machine inference, kept in `.openkos/` (already
swept by `purge`'s delete-and-rebuild). Decisions are irreplaceable human
judgment, kept in `bundle/.state/`, committed, reusing ADR-0013's
frontmatter-sidecar mechanism.

The two hazards inherited from ADR-0013 — `_autocommit`'s scoped staging
and `purge`/`forget`'s sweep coverage of the new `bundle/.state/**`
subtree — are specified as deltas against the shipped capabilities that
already own those contracts (`workspace-autocommit`, `privacy-purge`,
`forget-command`), not here, so the requirement lives where the next author
reading `_autocommit`/`purge`/`forget` will find it.

## Requirements

### Requirement: Contradiction Findings Are Persisted With Provenance

Each verdict a `curate` Contradictions stage run produces MUST be persisted
under `.openkos/`, keyed by its candidate pair identity, and MUST retain the
verdict, confidence, rationale, and a content digest of the inputs it was
computed from.

#### Scenario: A finding survives the process

- GIVEN one `curate` run completes the Contradictions stage
- WHEN a later, unrelated process reads the pending-work store
- THEN the same verdict, confidence, and rationale are readable without a
  new LLM call

### Requirement: Persisted Findings Are Rankable, And The Honesty Guard Is Preserved

`next` and `status` MUST be able to read the persisted finding set. A `next`
tier reading persisted findings MUST rank an open, non-stale, non-declined
contradiction as a candidate action. `next`'s `None`-action result MUST
continue to mean only "no ranked tier fired" and MUST NOT be read, stated,
or implied to mean the bundle is clean — this tier makes more findings
rankable; it MUST NOT license the inverse inference for findings that
remain unranked.

#### Scenario: An open contradiction is ranked

- GIVEN one open, non-stale, non-declined contradiction finding is persisted
- WHEN `next` runs and no higher-ranked tier fires
- THEN `next` returns that finding as its action

#### Scenario: An unranked finding does not become a false all-clear

- GIVEN a persisted finding exists but every `next` tier declines to fire
- WHEN `next` returns a `None` action
- THEN the printed result states only that no ranked tier produced a
  finding, and does not state or imply the bundle is clean

### Requirement: Declining Is A Non-Interactive Verb Keyed On Proposal Identity

An operator MUST be able to decline a specific contradiction finding through
a non-interactive command surface addressing it by a stable identity. That
identity MUST be derived from the candidate proposal — sorted `pair_ids`,
verdict kind, and `merged_absorbed_id` — and MUST NOT be derived from a
finding's storage row id. `merged_absorbed_id` MUST be part of the identity:
it is the sole discriminator between a typed-edge candidate and a
merged-body candidate; `pair_ids` shape alone is not a safe substitute.

#### Scenario: A declination survives recomputation

- GIVEN an operator declined a contradiction finding
- WHEN the Contradictions stage recomputes the same candidate pair later
- THEN the recomputed finding is recognized as already declined and does
  not reappear as open

#### Scenario: A typed-edge and a merged-body candidate over the same pair stay distinct

- GIVEN a typed-edge candidate and a merged-body candidate share the same
  `pair_ids`
- WHEN an operator declines one of them
- THEN only that candidate's decision is recorded, keyed by its own
  `merged_absorbed_id`, and the other candidate is unaffected

### Requirement: Declined Findings Are Hidden By Default, With An Explicit Listing View

A declined finding MUST NOT appear in ordinary `curate`, `status`, or `next`
output. An explicit command or flag MUST exist to list declined findings.

#### Scenario: A declined finding stays out of ordinary output

- GIVEN one contradiction finding was declined
- WHEN `curate`, `status`, or `next` runs without the declined-listing view
- THEN that finding does not appear in the output

#### Scenario: The declined-listing view surfaces it

- GIVEN one contradiction finding was declined
- WHEN the operator invokes the declined-listing view
- THEN that finding appears, identified and marked declined

### Requirement: Re-Opening A Declined Finding Requires Explicit Operator Action

A declined finding MUST NOT be reinstated automatically by any content
change to the concepts it was computed from. Reinstatement MUST require an
explicit operator action naming the finding.

#### Scenario: Content change does not silently reopen a decline

- GIVEN a declined finding, and one of its concepts is subsequently edited
- WHEN the finding is recomputed
- THEN it is marked stale, not reopened, and stays hidden from ordinary
  output

#### Scenario: Explicit re-open reinstates it

- GIVEN a declined contradiction finding
- WHEN the operator issues the explicit re-open action naming it
- THEN the finding is no longer treated as declined and is eligible to
  rank again

### Requirement: A Finding Is Invalidated Honestly When Its Inputs Change

A persisted finding MUST carry a content digest of the objects it was
computed from. When that digest no longer matches current bundle state, the
finding MUST be marked stale. A stale finding MUST NOT be presented as
current and MUST NOT be silently dropped.

#### Scenario: A changed concept marks its finding stale

- GIVEN a persisted finding computed over concept A's current content
- WHEN concept A's content changes
- THEN the finding is marked stale on next read, rather than shown as
  current

#### Scenario: A stale finding remains visible as stale

- GIVEN a finding marked stale
- WHEN `status` or the declined-listing view runs
- THEN the stale finding is shown labeled stale, not silently omitted
