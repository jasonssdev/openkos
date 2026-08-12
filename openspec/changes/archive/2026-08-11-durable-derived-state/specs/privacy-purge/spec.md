# Delta for Privacy Purge

Slice 1a.

## ADDED Requirements

### Requirement: Whole-History Expunge Covers The Ledger Sidecar Store

`purge`'s `git-filter-repo` rewrite MUST include, in the SAME single pass
as the existing whole-file expunge, every purge-set member's content
preserved under `bundle/.state/ledger/`: a survivor's own sidecar file (if
the purge-set member is a survivor) and any OTHER survivor's sidecar entry
whose snapshot fields embed the purge-set member's body or id (if the
purge-set member was absorbed by a prior merge). This coverage MUST NOT
introduce a second `git-filter-repo` invocation.

#### Scenario: Purging a merge survivor removes its ledger sidecar from history
- GIVEN a successful purge of a concept-id that is a merge survivor with a
  ledger sidecar under `bundle/.state/ledger/`
- WHEN the rewrite completes
- THEN that sidecar file is absent from `git rev-list --objects --all`,
  reflog, and `git cat-file` output, alongside the concept's own bundle
  file

#### Scenario: Purging a previously-absorbed concept removes its snapshot from another survivor's sidecar
- GIVEN a concept was absorbed by an earlier merge and its pre-merge body
  is preserved as an `absorbed_snapshot` in a different survivor's ledger
  sidecar
- WHEN `openkos purge <absorbed-concept-id>` completes
- THEN that snapshot's embedded body no longer appears in any commit's
  blob of the survivor's sidecar, and the rewrite ran in the same
  `git-filter-repo` pass as the concept's own file expunge

> **UNIMPLEMENTED, and UNREACHABLE under the current command surface
> (#573).** This scenario is not satisfied and is recorded here rather than
> left silent: an unimplemented privacy scenario that reads as implemented
> is worse than a tracked gap.
>
> **What is implemented.** `purge` expunges each purge-set member's OWN
> sidecar (`bundle_ledger.ledger_path_for(member, ...)`, `cli/main.py`).
> A cross-survivor `absorbed_snapshot` — the absorbed concept's body living
> inside a DIFFERENT survivor's sidecar — is not a whole-file expunge
> target and is not reached.
>
> **Why it is not a live leak.** The scenario's own precondition is
> unreachable: `openkos purge <absorbed-concept-id>` cannot run. `purge`
> resolves its root id through `_resolve_concept_path`, which refuses with
> `ValueError` when `<canonical_id>.md` does not exist, and an absorbed
> concept's file is gone once the merge completes. No supported command can
> put the system in the state this scenario describes.
>
> **What would make it reachable.** Any verb that learns to address an
> ABSORBED id — that is, any change relaxing or bypassing
> `_resolve_concept_path`'s existence gate for a ledger-known id. The
> concrete candidate is `unmerge --to <id>` (#562), which reads the same
> ledger. Whoever implements that MUST close this gap in the same change,
> or the existence gate stops being the thing that makes this safe.

#### Scenario: Unrelated sidecar entries are untouched
- GIVEN a survivor's ledger sidecar holds entries for both the purge-set
  member and an unrelated concept
- WHEN the purge completes
- THEN the unrelated entry remains byte-identical in every historical
  commit
