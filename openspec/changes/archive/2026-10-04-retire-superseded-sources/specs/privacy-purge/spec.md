# Delta for Privacy Purge

## MODIFIED Requirements

### Requirement: Purge Set Resolution Reuses Forget Phase A

`purge <concept-id>` MUST accept `--scope {self,source}` (default `self`) and
MUST resolve the purge set using `forget`'s existing pure Phase A: concept-id
path-safety/resolution, `--scope source` Provenance Descendant Resolution
(orphan-after-delete fixed point), and reference-aware detection, unchanged.
`purge` MUST NOT take `forget`'s retire path ("Retiring A Superseded Source
Detaches Its Historical References" in `forget-command`): a `supersedes`
relation or a generated provenance entry or `## Related` bullet that refers
to a purge-set Source remains an external reference that blocks `purge`
exactly as before, and `purge` MUST NOT rewrite any document outside the
purge set.

#### Scenario: Self scope purge set is one concept
- GIVEN `openkos purge <concept-id>` with no `--scope` flag
- WHEN Phase A resolves the purge set
- THEN it contains exactly `<concept-id>`

#### Scenario: Source scope cascades to orphaned descendants
- GIVEN Source X and a concept C with `provenance: [X]` only
- WHEN `openkos purge X --scope source` runs
- THEN the purge set contains X and C, and X's `raw/<name>` plus both
  `bundle/<id>.md` paths are targeted for history expunge — C, a derived
  concept with no `resource` of its own, contributes no raw path

#### Scenario: Purge does not take the retire path
- GIVEN `sources/v2` has `supersedes -> sources/v1`
- WHEN `openkos purge sources/v1 --scope source` runs
- THEN the `supersedes` relation is an external reference that refuses the
  purge as before, and no document outside the purge set is rewritten
