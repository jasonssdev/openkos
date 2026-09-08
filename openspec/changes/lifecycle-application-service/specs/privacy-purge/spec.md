# Delta for Privacy Purge

## Purpose Update (for archive-time merge into the main spec's `## Purpose`)

Replace the current Purpose paragraph in `openspec/specs/privacy-purge/spec.md`
with:

`openkos purge <concept-id>` is the irreversible, true-erasure counterpart to
`forget`: it whole-file-expunges a concept's source `raw/<name>` and bundle
file from ALL git history (not just the working tree) via `git-filter-repo`.
Slice 1 is honest whole-file erasure with a named residual; it does not
claim complete right-to-be-forgotten. `purge` itself owns argument parsing,
workspace and configuration setup, the confirmation gate, rendering the
"IRREVERSIBLE history rewrite" disclosure from the templates the lifecycle
application service returns, byte-for-byte, and invoking `git-filter-repo`;
purge-set resolution, the fail-closed safety rails, and the disclosure
content itself are composed by the lifecycle application service.

(Previously: this paragraph described `purge` as performing the purge-set
resolution, safety-rail evaluation, and disclosure composition itself —
that composition now lives behind the lifecycle application service.)

## Notes

No `## Requirements` entries in the main spec change. This is a composition
refactor: purge-set resolution reusing forget's Phase A, the fixed-order
fail-closed safety rails, and the disclosure wording — every behavior
specified in `openspec/specs/privacy-purge/spec.md` — are unchanged by
moving their composition behind the service. The existing
`tests/unit/cli/test_purge.py` suite (76 tests) remains the behavior
contract, and `lifecycle-application-service`'s "Purge's Disclosure Renders
From Returned Templates Byte-For-Byte" and "The Extraction Preserves
Observable CLI Behavior" requirements are the checkable guarantees tying the
two specs together.
