# Delta for Reconcile Command

## RENAMED Requirements

### Requirement: Additive-Only, No Status/Lifecycle Write → Additive-Only, Status Written Only As The Supersedes Export

(Reason: a `supersedes` write now also writes the superseded concept's deprecated-status export (issue #1075), so "no status write" is no longer true and the name must say what the one permitted status write is.)
(Migration: None — existing bundles are made export-consistent by `openkos repair`; edges, notes, and log lines are written exactly as before.)

## MODIFIED Requirements

### Requirement: Additive-Only, Status Written Only As The Supersedes Export

The system MUST NOT delete or overwrite existing body content or relations;
all writes to body and relations MUST be additive (new edge, appended note,
appended log line). The ONE permitted status write is the deprecated-status
export (`deprecated-status-export`): WHEN this invocation ADDS a
`supersedes` edge, the system MUST evaluate the export projection for the
superseded counterpart over the pair's post-write relations and write its
outcome into that counterpart's document in the SAME Phase B write, under
the SAME drift guard and the SAME autocommit as the edge. A symmetric
(`reconciled_with`) or `--revision` (`revises`) reconcile MUST NOT write
any `status` or `status_derived_from` field, and neither MUST an idempotent
re-run that adds no edge (pre-existing export drift is `repair`'s concern).
A BLOCKED outcome (a human-authored `status` such as `draft`) MUST leave
that `status` untouched while the edge is still written. The Phase A
preview MUST name the counterpart's status change (`status → deprecated`)
or, when BLOCKED, state that its own `status` is preserved and that it is
hidden from retrieval regardless. The write path MUST NOT invoke
contradiction detection or any LLM.
(Previously: the system MUST NOT write any `status`/deprecate field; deprecation was visible only through the `supersedes` edge.)

#### Scenario: Existing content preserved

- GIVEN `alpha` has prior unrelated body content and relations
- WHEN the user runs `reconcile alpha beta` and confirms
- THEN all prior body content and relations on `alpha` remain unchanged
- AND only the new edge and note are appended

#### Scenario: A directional supersede exports the loser's status

- GIVEN `alpha` and `beta` are unreconciled and `beta` carries
  `status: stable`
- WHEN the user runs `reconcile alpha beta --winner alpha` and confirms
- THEN `alpha` gains the `supersedes` edge to `beta`
- AND `beta` carries `status: deprecated` and `status_derived_from:
  supersedes`, written in the same commit as the edge
- AND the preview named `beta`'s status change before the confirm gate

#### Scenario: A loser with a human-authored draft keeps it

- GIVEN `beta` carries `status: draft`
- WHEN the user runs `reconcile alpha beta --winner alpha` and confirms
- THEN the `supersedes` edge is written and `beta`'s `status` stays `draft`
- AND the preview stated that `beta`'s own status is preserved

#### Scenario: Symmetric and revision reconciles write no status

- GIVEN `alpha` and `beta` carry `status: stable`
- WHEN the user runs `reconcile alpha beta`, or `reconcile alpha beta
  --revision alpha`, and confirms
- THEN neither document gains or changes `status` or `status_derived_from`

#### Scenario: A declined supersede writes no status

- GIVEN `beta` carries `status: stable`
- WHEN the user runs `reconcile alpha beta --winner alpha` and declines
  the confirm gate
- THEN `beta`'s bytes are unchanged
