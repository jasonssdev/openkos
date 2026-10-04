# Delta for List Command

## MODIFIED Requirements

### Requirement: Deprecated and Superseded Visibility

`openkos list` MUST show deprecated and superseded objects by default,
marked with their status, with no flag to hide them. The `STATUS` it reports
MUST agree with the shared effective-status predicate
(`status-aware-retrieval`), including a concept that is deprecated only as a
provenance orphan of a superseded Source, and MUST compute it from the same
single bundle walk (it MUST NOT add a second walk). Objects deleted from
disk by `merge` (`src/openkos/bundle/merge.py`) are absent from the walk
and therefore never appear as a distinct "merged" row.

#### Scenario: Deprecated object shown by default
- GIVEN a bundle containing an object marked `deprecated`
- WHEN `openkos list` runs
- THEN the object is printed with `STATUS` = `deprecated`, with no flag
  required

#### Scenario: A provenance orphan is listed as deprecated
- GIVEN `sources/v2` supersedes `sources/v1` and concept C has `provenance:
  [sources/v1]` only
- WHEN `openkos list` runs
- THEN C is printed with `STATUS` = `deprecated`

#### Scenario: list and the retrieval predicate agree
- GIVEN any bundle
- WHEN `list` STATUS values and `lifecycle.deprecated_concept_ids` are
  computed over it
- THEN the set of `deprecated` rows equals the set the predicate returns
