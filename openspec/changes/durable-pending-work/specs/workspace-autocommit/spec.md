# Delta for Workspace Autocommit

## MODIFIED Requirements

### Requirement: Scoped Staging Only

`_autocommit` MUST stage with `git add -- <paths>` and MUST NOT use `-A` or
`-a`. A pre-existing unrelated dirty file elsewhere in the workspace MUST
NOT be swept into the commit. A decline or re-open of a persisted
contradiction finding writes a `bundle/.state/**` decision path; that path
MUST be added explicitly to the caller's path list passed to `_autocommit`,
the same way `MergeResult.ledger_sidecar_path` is added for a merge. A
decision path not explicitly listed MUST NOT be picked up implicitly and
MUST NOT enter the commit.
(Previously: scoped-staging behavior only, no explicit link to the
pending-work decision path.)

#### Scenario: Unrelated dirty file is left untouched

- GIVEN a git-backed workspace with an unrelated pre-existing dirty
  (modified but uncommitted) file, and configured git identity
- WHEN a mutating verb completes successfully and `_autocommit` runs
- THEN the resulting commit contains only the verb's own written paths
- AND the unrelated dirty file remains modified and uncommitted after the
  command exits

#### Scenario: A decline's decision path is staged explicitly

- GIVEN an operator declines a persisted contradiction finding, writing one
  `bundle/.state/**` decision path
- WHEN the decline command's `_autocommit` call runs
- THEN that decision path appears in the committed path set
- AND no other unrelated dirty path is swept into the commit

#### Scenario: An un-listed decision path never enters git

- GIVEN a decision path was written to `bundle/.state/**` but was NOT added
  to the caller's `_autocommit` path list
- WHEN `_autocommit` runs
- THEN that path is not staged and does not appear in the resulting commit
