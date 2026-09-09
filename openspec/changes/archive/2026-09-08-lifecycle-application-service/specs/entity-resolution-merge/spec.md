# Delta for Entity-Resolution Merge

## Purpose Update (for archive-time merge into the main spec's `## Purpose`)

Replace the current Purpose paragraph in
`openspec/specs/entity-resolution-merge/spec.md` with:

`entity-resolution-merge` is the first DESTRUCTIVE entity-resolution
capability: a confirm-gated, fully REVERSIBLE 2-way `merge` of two
concept-ids a human has confirmed are the same entity, plus a first-class
`unmerge` with round-trip parity. `merge` and `unmerge` each own argument
parsing, workspace setup, the confirmation gate, rendering the preview and
result, and the catalog/log write via the shared write helpers; the Phase A
(prepare) / confirm-gate / Phase B (write) composition for both commands —
including `unmerge`'s newly public `prepare_unmerge`/`unmerge_core` pair,
matching `merge`'s existing shape — is delegated to the lifecycle
application service.

(Previously: this paragraph did not name a composition layer; `merge`'s
`prepare_merge`/`merge_core` were already pure but un-relocated, and
`unmerge` had no public prepare/core pair. Both now compose behind the
lifecycle application service.)

## Notes

No `## Requirements` entries in the main spec change. This is a relocation
and composition refactor: fusion semantics, provenance union, reversibility,
ledger snapshot restoration, and every other behavior specified in
`openspec/specs/entity-resolution-merge/spec.md` are unchanged by relocating
`prepare_merge`/`merge_core` and adding `unmerge`'s symmetric pair behind
the service. The existing `tests/unit/cli/test_merge_core.py`,
`test_merge.py`, `test_unmerge.py`, and `test_unmerge_surgical_catalog.py`
suites remain the behavior contract, and
`lifecycle-application-service`'s "Unmerge Has A Full Public Prepare/Core
Pair Matching Merge" and "The Extraction Preserves Observable CLI Behavior"
requirements are the checkable guarantees tying the two specs together.
