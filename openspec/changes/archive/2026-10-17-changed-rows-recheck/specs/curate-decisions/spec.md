# Delta for curate-decisions

## MODIFIED Requirements

### Requirement: Staleness Is Marked, Never Hidden

The list MUST return open rows whose inputs have changed since they were
computed, marked stale, rather than omitting them. A stale item MUST NOT be
applied silently: an apply against it MUST return the typed Stale result.

A preview MUST agree with the apply about a stale item: previewing any write
action of an open row whose inputs have changed MUST return the typed Stale
result `row_inputs_changed`, the reason the apply returns, and MUST write
nothing. An action the item does not take MUST still be refused as not applied,
and skip, which writes nothing, MUST still return its not-applied result. The
list MUST keep reporting the stale item's actions unchanged.

Freshness MUST be computed for the kinds that carry actions (`identity`,
`relation_type` and `volatility`). A kind that carries no actions in this change
(`contradiction`, `revision` and `watch_refusal`) MUST report freshness as not computed (`fresh` is `None`, never `True`), while the kinds that carry actions report `True` or `False`.

#### Scenario: A row whose input changed is listed as stale

- GIVEN an open identity row and a member concept edited since the row was
  computed
- WHEN a client lists the open decisions
- THEN the row is present and marked stale

#### Scenario: An action-less kind reports freshness as not computed

- GIVEN an open contradiction, revision or watch_refusal item
- WHEN a client lists the open decisions
- THEN the item's freshness is reported as not computed, not as fresh

#### Scenario: A stale item's preview is the apply's Stale

- GIVEN an open judged-same identity row whose member was edited since the row
  was computed
- WHEN a client previews merge or keep-distinct on it
- THEN the preview returns Stale `row_inputs_changed` and nothing is written

#### Scenario: Skip on a stale item is still skip

- GIVEN an open stale row
- WHEN a client previews skip
- THEN the preview returns the not-applied result `skipped`
