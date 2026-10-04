# Delta for Deprecated-Status Export

## ADDED Requirements

### Requirement: A Provenance-Derived Deprecation Is Not Exported

The deprecated-status export MUST remain a projection of `supersedes` edges
only. A concept that is effective-deprecated solely as a provenance orphan of
a superseded Source (`status-aware-retrieval`) MUST NOT be exported:
`relate`, `unrelate`, `forget`, `merge`, `reconcile` and `repair` MUST NOT
write `status: deprecated` or `status_derived_from` to it, MUST NOT
withdraw one, and `lint`'s drift scan MUST NOT report it. The input to the
projection (`lifecycle.superseded_from_metadata`) MUST stay the edge-derived
set. An external OKF v0.2 reader therefore sees such a concept without a
deprecated status; the engine's own retrieval, `list`, and judgments see it
as deprecated.

#### Scenario: relate exports the Source and writes nothing to its concepts
- GIVEN `sources/v1` has two sole-source concepts
- WHEN `relate sources/v2 supersedes sources/v1` is confirmed
- THEN `sources/v1` carries the export, and neither concept's bytes changed

#### Scenario: repair leaves a provenance orphan alone
- GIVEN a concept deprecated only as a provenance orphan, with `status:
  stable`
- WHEN `openkos repair` runs
- THEN the concept's bytes are unchanged and no export is counted for it

#### Scenario: lint reports no drift for a provenance orphan
- GIVEN the same concept
- WHEN `openkos lint` runs
- THEN the deprecated-status drift scan reports nothing for it

## MODIFIED Requirements

### Requirement: The Engine Never Reads Its Own Export

The effective-status predicate (`lifecycle.deprecated_concept_ids`) and
every consumer of `okf.declares_deprecated` MUST treat a `status:
deprecated` carrying a VALID marker as NOT declared by the concept itself;
only a `status: deprecated` without a valid marker counts as a human
declaration. A concept's effective deprecation therefore depends only on
`supersedes` edges, provenance orphanhood of a superseded Source, and
human-authored status, never on an export, so a stale, missing, or
partially-written export can misinform an external reader but can never
change what OpenKOS retrieves, lists, or judges.

#### Scenario: A stale export does not hide a concept

- GIVEN concept `b` with `status: deprecated` and a valid marker, and no
  `supersedes` edge targeting `b`
- WHEN `lifecycle.deprecated_concept_ids` runs
- THEN `b` is not in the result, and `list` reports `b` as `stable`

#### Scenario: A missing export does not un-hide a concept

- GIVEN concept `b` with `status: stable`, targeted by `a`'s `supersedes`
  edge
- WHEN `lifecycle.deprecated_concept_ids` runs
- THEN `b` is in the result

#### Scenario: A provenance orphan is hidden with no export present

- GIVEN concept `c` with `status: stable` and `provenance: [sources/v1]`,
  and `sources/v2` supersedes `sources/v1`
- WHEN `lifecycle.deprecated_concept_ids` runs
- THEN `c` is in the result although `c` carries no export
