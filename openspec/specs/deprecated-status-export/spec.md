# Deprecated-Status Export Specification

## Purpose

OpenKOS decides that a concept is deprecated from `supersedes` edges
(`status-aware-retrieval`). An external OKF v0.2 consumer reads only the
superseded concept's own frontmatter, where `status` is absent or `stable`,
so it cannot tell that the concept is no longer current. This
capability defines the deprecated-status EXPORT: a deterministic projection
of the computed supersession onto the superseded concept's frontmatter
`status`, marked as engine-derived so the engine never reads its own export
back. It owns the projection rule, its precedence over human-authored
values, the drift it can have, and how `openkos repair` restores
consistency from any state. Which verbs apply the projection at write time
is owned by each verb's own capability (`reconcile-command`,
`typed-relationships`, `forget-command`, `privacy-purge`,
`entity-resolution-merge`); the read-only drift report is owned by `lint`.

## Non-Goals

This spec does not define: an `unreconcile` verb (a path with no reversal
verb today is recovered by editing the edge away and running `repair`);
making frontmatter `status` the source of truth for deprecation (edges stay
the authority); writing `status: draft` from any path; recording WHICH
concepts supersede a concept in its own frontmatter; any change to how
`revises` or `reconciled_with` affect status (neither deprecates); or
changing retrieval, `list`, `concept_read`, or MCP output for any concept.

## Requirements

### Requirement: The Export Marker Is An Engine-Owned Extension Key

A concept whose `status: deprecated` was written by the engine as an export
MUST carry the extension key `status_derived_from` with the exact string
value `supersedes` (OKF v0.2 §4.1 extension). The marker is VALID only when
its value is exactly `supersedes` AND the same document's `status` is
exactly `deprecated`. Every other combination — a marker with any other
value, or a `supersedes` marker beside any `status` other than
`deprecated` — is an INVALID marker. All knowledge of the key name, its
value, and its validity MUST live in `model/okf.py` and nowhere else.
Removing the marker by hand is how a person claims an exported
`status: deprecated` as their own.

#### Scenario: A valid marker

- GIVEN a concept with `status: deprecated` and `status_derived_from: supersedes`
- WHEN its marker is evaluated
- THEN the marker is valid and the `status` is an engine export

#### Scenario: A marker beside a non-deprecated status is invalid

- GIVEN a concept with `status: stable` and `status_derived_from: supersedes`
- WHEN its marker is evaluated
- THEN the marker is invalid

#### Scenario: A marker with an unknown value is invalid

- GIVEN a concept with `status: deprecated` and `status_derived_from: manual`
- WHEN its marker is evaluated
- THEN the marker is invalid and the `status: deprecated` is human-authored

### Requirement: One Deterministic Projection Decides Every Export Write

Every path that writes, withdraws, or repairs a deprecated-status export
MUST decide the outcome through ONE pure function in `model/okf.py` that
takes a concept's own frontmatter and whether that concept is superseded
(the target of at least one non-self `supersedes` edge authored by another
concept) and returns exactly one outcome. The outcome MUST follow this
table, evaluated after discarding an INVALID marker:

| Superseded | Own `status` (marker discarded if invalid) | Valid marker | Outcome |
|---|---|---|---|
| yes | absent, `stable`, or legacy `active` | no | EXPORT — set `status: deprecated` and `status_derived_from: supersedes` |
| yes | `deprecated` | yes or no | UNCHANGED |
| yes | any other value (e.g. `draft`) | no | BLOCKED — nothing written; the value is preserved and reported |
| no | `deprecated` | yes | WITHDRAW — set `status: stable` and remove the marker |
| no | `deprecated` | no | UNCHANGED (human-authored) |
| no | any other value or absent | no | UNCHANGED |

When the input carried an INVALID marker, the outcome MUST also remove that
marker; when the table row is otherwise UNCHANGED, that removal alone is
the outcome (DROP-MARKER). The function MUST preserve every other key and
the body byte-for-byte, and MUST be idempotent: applying its result and
re-evaluating yields UNCHANGED or BLOCKED.

#### Scenario: A superseded stable concept is exported

- GIVEN concept `b` with `status: stable` and no marker, targeted by
  `a`'s `supersedes` edge
- WHEN the projection is evaluated for `b`
- THEN the outcome is EXPORT, and the result carries `status: deprecated`
  and `status_derived_from: supersedes` with every other key unchanged

#### Scenario: A human-authored draft is never overwritten

- GIVEN concept `b` with `status: draft`, targeted by a `supersedes` edge
- WHEN the projection is evaluated for `b`
- THEN the outcome is BLOCKED and `b`'s frontmatter is unchanged

#### Scenario: An export whose edge is gone is withdrawn

- GIVEN concept `b` with `status: deprecated` and a valid marker, and no
  `supersedes` edge targeting `b`
- WHEN the projection is evaluated for `b`
- THEN the outcome is WITHDRAW, and the result carries `status: stable`
  and no `status_derived_from` key

#### Scenario: A human-authored deprecation is never withdrawn

- GIVEN concept `b` with `status: deprecated`, no marker, and no
  `supersedes` edge targeting `b`
- WHEN the projection is evaluated for `b`
- THEN the outcome is UNCHANGED

#### Scenario: An invalid marker on a live concept is dropped alone

- GIVEN concept `b` with `status: draft`, `status_derived_from: supersedes`,
  and no `supersedes` edge targeting `b`
- WHEN the projection is evaluated for `b`
- THEN the outcome is DROP-MARKER: the marker is removed and `status: draft`
  is kept

#### Scenario: The projection is idempotent

- GIVEN any concept frontmatter and any superseded value
- WHEN the projection's result is evaluated again with the same superseded
  value
- THEN the second outcome is UNCHANGED or BLOCKED

### Requirement: The Engine Never Reads Its Own Export

The effective-status predicate (`lifecycle.deprecated_concept_ids`) and
every consumer of `okf.declares_deprecated` MUST treat a `status:
deprecated` carrying a VALID marker as NOT declared by the concept itself;
only a `status: deprecated` without a valid marker counts as a human
declaration. A concept's effective deprecation therefore depends only on
`supersedes` edges and human-authored status, never on an export, so a
stale, missing, or partially-written export can misinform an external
reader but can never change what OpenKOS retrieves, lists, or judges.

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

### Requirement: Withdrawal Requires A Complete Edge Walk

The superseded set MUST be computed from every concept document's
`relations:`. WHEN any concept document fails to read or parse, or carries
malformed `relations:`, the superseded set is incomplete, and any path
evaluating the projection over it MUST NOT WITHDRAW an export on that basis:
an unreadable document may hold the only edge that supersedes it. EXPORT,
BLOCKED, and DROP-MARKER outcomes remain safe on an incomplete walk, since
each is justified by an edge or a marker actually observed. A skipped
withdrawal MUST be reported, naming the affected concept and at least one
unreadable document.

#### Scenario: An unreadable document blocks a withdrawal

- GIVEN concept `b` with a valid export, no readable `supersedes` edge
  targeting it, and one concept document that fails to parse
- WHEN any path evaluates the projection for `b` over that bundle
- THEN `b`'s export is kept and the skip is reported naming `b` and the
  unreadable document

### Requirement: `repair` Restores Export Consistency From Any State

`openkos repair` MUST evaluate the projection for every concept in the
bundle over a complete edge walk and rewrite every concept whose outcome is
EXPORT, WITHDRAW, or DROP-MARKER, in the SAME run and commit as its other
migrations, and within its existing refusal gates and drift guard. A
document that also needs OKF v0.1→v0.2 migration MUST be migrated first
and projected second, as ONE rewrite. BLOCKED concepts MUST NOT be written
and MUST be reported by count and id. Withdrawals skipped under "Withdrawal
Requires A Complete Edge Walk" MUST be reported. `repair`'s summary MUST
count exports written, exports withdrawn, and markers dropped separately,
and its "nothing to migrate" report MUST account for export consistency. A
second `repair` run immediately after a successful one MUST write nothing.

#### Scenario: repair exports a superseded concept the edge was hand-written for

- GIVEN `a` holds a hand-written `supersedes` edge to `b`, and `b` carries
  `status: stable`
- WHEN `openkos repair` runs
- THEN `b` carries `status: deprecated` and `status_derived_from:
  supersedes`, and the summary counts one export written

#### Scenario: repair withdraws an export whose edge was removed by hand

- GIVEN `b` carries a valid export and the only `supersedes` edge to `b`
  was deleted by hand
- WHEN `openkos repair` runs
- THEN `b` carries `status: stable` and no marker, and the summary counts
  one export withdrawn

#### Scenario: repair leaves a blocked draft alone and says so

- GIVEN `b` carries `status: draft` and is targeted by a `supersedes` edge
- WHEN `openkos repair` runs
- THEN `b` is not written, and the report names `b` as blocked by its own
  `status: draft`

#### Scenario: repair is idempotent over exports

- GIVEN a bundle `openkos repair` just made export-consistent
- WHEN `openkos repair` runs again
- THEN it writes nothing and creates no commit
