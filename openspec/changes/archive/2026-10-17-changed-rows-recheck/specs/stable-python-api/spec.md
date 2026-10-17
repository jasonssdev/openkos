# Delta for stable-python-api

## MODIFIED Requirements

### Requirement: Refreshing The Decision Queue Is Exposed As A Model-Free Operation

The API MUST expose `refresh_decision_queue`, taking a `Workspace`, and
returning a result that reports, per kind (`identity`, `relation_type`,
`volatility`), what the pass did: rows inserted, amended, unchanged, replaced,
suppressed and retired, rows retired because they name a concept no longer in
the bundle (`retired_missing`), and whether the pass was complete for that kind;
the volatility entry additionally counts the types skipped because the two
confidentiality regimes disagreed, and the result carries the identity
truncation notice when the identity report was truncated. Its behavior is
specified under the `pending-work` capability ("The Decision Queue Can Be
Refreshed Without A Model"); this capability requires only that the API exposes
it, that it builds no backend client, that it takes the lock only inside commit
sections (waiting at most the caller's `wait_seconds`, default zero, and raising
the existing workspace-busy error beyond it), and that it returns the per-kind
result. Only the identity entry can report a retirement or a complete pass; the
other two always report `complete` false. Only the identity and relation_type
entries can report `retired_missing`; the volatility entry reports zero. The
field is additive within `API_VERSION` 1. A workspace that cannot be read raises
the public queue-refresh refusal and writes nothing.

#### Scenario: Refresh needs no backend

- GIVEN a workspace whose configured backend is unreachable
- WHEN `refresh_decision_queue` is called
- THEN it completes and no backend client is built

#### Scenario: The result is reported per kind

- GIVEN a refresh over a workspace with persisted verdicts of all three kinds
- WHEN the result is read
- THEN it carries one entry per kind with its inserted, amended and retired
  counts and its completeness

#### Scenario: Only identity can report a complete pass

- GIVEN any refresh
- WHEN the result is read
- THEN the relation_type and volatility entries report `complete` false and
  zero retired

#### Scenario: Rows naming a missing page are counted apart

- GIVEN an open identity row and an open relation-type row, each naming a
  concept whose file is gone
- WHEN `refresh_decision_queue` is called
- THEN the identity and relation_type entries each report `retired_missing` 1
