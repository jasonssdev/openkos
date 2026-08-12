# Delta for Privacy Purge

## ADDED Requirements

### Requirement: Whole-History Expunge Covers The Pending-Work Decision Subtree

`purge`'s `git-filter-repo` rewrite MUST include, in the SAME single pass as
the existing whole-file expunge, every `bundle/.state/**` decision path
(contradiction decline/re-open) that references a purge-set member's
concept id, either as a member of the decision's proposal identity or as
the concept the decision was recorded against. This coverage MUST NOT
introduce a second `git-filter-repo` invocation, and follows the same
INCLUDE-walk pattern ADR-0013 established for the merge-ledger sidecar.

#### Scenario: Purging a concept removes its decision from history

- GIVEN a `bundle/.state/**` decision file references a concept id that is
  subsequently purged
- WHEN the purge rewrite completes
- THEN no historical commit's blob of that decision path contains the
  purged concept's id, verified by `git rev-list --objects --all` and `git
  cat-file`, and the rewrite ran in the same `git-filter-repo` pass as the
  concept's own file expunge

#### Scenario: An unrelated decision entry is untouched

- GIVEN a decision path references a concept unrelated to the purge set
- WHEN the purge completes
- THEN that decision's content remains byte-identical in every historical
  commit
