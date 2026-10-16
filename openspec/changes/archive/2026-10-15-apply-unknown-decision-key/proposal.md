# Proposal: Report A Never-Existing Decision Key As Unknown

Tracking issue: #1404. Governing decision: ADR-0060.

## Intent

`apply_decision` and `preview_decision` returned `Stale` with reason
`row_resolved` both for a decision whose queue row was settled and for a key no
row ever carried. A caller showing "this decision was already resolved" for a
mistyped or fabricated key reported something that did not happen.

## Scope

- Add the `Stale` reason `unknown_key`, returned when no queue row of any status
  carries the key. `row_resolved` keeps meaning a settled or retired row.
- A queue that exists but cannot be read is never reported as `unknown_key`.
- The new reason is an additive `Literal` widening, so `API_VERSION` stays 1.
- Amend the `stable-python-api` and `curate-decisions` requirements and
  regenerate the surface snapshot.

## Out of scope

The commit-section checks that return `row_resolved` when a row vanishes between
the preview and the commit: there the row did exist.
