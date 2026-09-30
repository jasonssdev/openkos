# OKF Format Migration Specification

## Purpose

OpenKOS adopts OKF v0.2, which
retires two v0.1 fields — the body `# Citations` list and `timestamp` — in
favor of frontmatter `sources` and `generated: { by, at }`, and standardizes
lifecycle as `status: draft | stable | deprecated`. This capability owns two
things: the explicit, reviewable in-place migration of an existing v0.1
bundle to v0.2 via `openkos repair`, and the cross-cutting dual-reader
compatibility guarantees that let every consumer of the bundle — retrieval,
`list`, `lint`, the typed graph, merge, and the MCP surface — treat a v0.1
bundle, a v0.2 bundle, and a bundle mixing both shapes identically. Writer
behavior for FRESH concepts (what `ingest` emits) is owned by the
`ingestion` capability; this capability owns the READ-side compatibility
contract those writers rely on, and the migration that moves an existing
bundle from one shape to the other.

## Non-Goals

This spec does not define: a separate `migrate` verb (the migration is an
extension of the existing `repair` verb); lazy or on-read migration of
existing bundles; writing `status: draft` from any writer; how a superseded
concept's `status: deprecated` is written (that is the marked export
defined by `deprecated-status-export`; deprecation itself stays derived
from `supersedes` edges at read time, per `status-aware-retrieval`); `verified`, trust tiers, `stale_after`,
`usage_count`/`usage_window`, or Attested Computation (OKF v0.2 families
this change does not adopt); per-claim `[^id]` footnotes in bodies (`##
Related` remains the attribution surface); or a `doctor`/`status` advisory
pointing an unmigrated bundle at `repair` (a candidate follow-up — reader
compatibility already makes an unmigrated bundle safe to use as-is).

## Requirements

### Requirement: Generation-Time Resolution With Legacy `timestamp` Fallback

The system MUST provide one shared helper, used by every reader that needs
a concept's generation time (including `merge_metadata`'s most-recent-wins
rule and `query-answer`'s revision-history date resolution), that resolves
the value from `generated.at` when present, and falls back to a legacy
`timestamp` value when `generated` is absent. No reader outside this helper
MUST read `generated.at` or `timestamp` directly for this purpose.

#### Scenario: A v0.2 concept resolves via `generated.at`

- GIVEN a concept carrying `generated: { at: <T> }` and no `timestamp`
- WHEN its generation time is resolved
- THEN the resolved value is `<T>`

#### Scenario: A legacy concept resolves via `timestamp`

- GIVEN a concept carrying a legacy `timestamp: <T>` and no `generated` key
- WHEN its generation time is resolved
- THEN the resolved value is `<T>`

#### Scenario: `generated.at` takes precedence when both are present

- GIVEN a concept carrying both `generated: { at: <T1> }` and a legacy
  `timestamp: <T2>`, with `<T1> != <T2>`
- WHEN its generation time is resolved
- THEN the resolved value is `<T1>`, the `generated.at` value

### Requirement: Legacy Lifecycle Values Are Not Deprecated

Every consumer that reads a concept's lifecycle `status` MUST treat
`"stable"`, a legacy `"active"`, `"draft"`, and an absent `status` key
identically as NOT deprecated. Only the literal value `"deprecated"`
WITHOUT a valid `status_derived_from` export marker, and the existing
inbound-`supersedes`-edge rule (`status-aware-retrieval`), MUST mark a
concept deprecated. A `"deprecated"` carrying a valid export marker is the
engine's deprecated-status export (`deprecated-status-export`), derived
from that edge rule and never read back as a declaration of its own.
(Previously: the literal `"deprecated"` always counted; no engine path wrote it, so no export marker existed.)

#### Scenario: A legacy `active` concept is not deprecated

- GIVEN a concept with `status: active` and no inbound `supersedes` edge
- WHEN its effective status is resolved by any consumer
  (`lifecycle.deprecated_concept_ids`, `list`, `concept_read`, the MCP
  concept payload)
- THEN it is reported as not deprecated

#### Scenario: A `stable` concept is not deprecated

- GIVEN a concept with `status: stable` and no inbound `supersedes` edge
- WHEN its effective status is resolved
- THEN it is reported as not deprecated

#### Scenario: An absent status is not deprecated

- GIVEN a concept with no `status` key at all
- WHEN its effective status is resolved
- THEN it is reported as not deprecated

#### Scenario: An exported `deprecated` is not a declaration

- GIVEN a concept with `status: deprecated` and `status_derived_from:
  supersedes`, and no inbound `supersedes` edge
- WHEN its effective status is resolved by any consumer
- THEN it is reported as not deprecated
### Requirement: Mixed-Bundle Read Parity

For a bundle containing any combination of OKF v0.1-shaped concepts
(`timestamp`, `status: active`, a legacy `# Citations` body section),
OKF v0.2-shaped concepts (`generated`, `status: stable`, `sources`), and
concepts of either shape, every read-side surface — retrieval (`query`),
`list`, `lint`, the typed graph, and the MCP concept payload — MUST return
results identical in structure and content to what it would return if every
concept in that bundle were already migrated to v0.2, except for the
byte-level shape of the frontmatter fields themselves.

#### Scenario: A mixed bundle answers identically to its fully-migrated twin

- GIVEN a bundle with some concepts in v0.1 shape and others already
  migrated to v0.2, and a fully-migrated twin of the same bundle
- WHEN `query`, `list`, `lint`, and the typed graph run against both
- THEN their results are identical in content — matched concepts, ranking,
  lint findings, and graph edges — modulo the on-disk frontmatter shape

### Requirement: `repair` Migrates An OKF v0.1 Bundle To v0.2 In One Commit

`openkos repair` MUST extend its existing migration behavior to rewrite
every OKF v0.1-shaped concept in the bundle to v0.2 shape: legacy
`timestamp` becomes `generated: { by: openkos/legacy, at: <the old
timestamp value, unchanged> }`; `status: active` becomes `status: stable`;
`sources` is (re)generated from `provenance`, per the projection
`ingestion`'s "`sources` Is A Generated, One-Way Projection Of
`provenance`" requirement defines; and the bare, engine-written empty
`# Citations` heading is removed from the body. The bundle-root
`index.md`'s `okf_version` MUST flip from `"0.1"` to `"0.2"` in the SAME
commit as these concept rewrites — never a separate, later step — so the
bundle never declares v0.2 while any of its concepts remain v0.1-shaped.

#### Scenario: A v0.1 concept is rewritten to v0.2 shape

- GIVEN a Source concept carrying `timestamp: '2026-07-14T09:00:00Z'` and
  `status: active`, with a bare empty `# Citations` heading
- WHEN `openkos repair` runs
- THEN that concept now carries `generated: { by: openkos/legacy, at:
  '2026-07-14T09:00:00Z' }` and `status: stable`, its `sources` list matches
  its `provenance`, and the `# Citations` heading is gone

#### Scenario: `okf_version` flips in the same commit as the concept rewrites

- GIVEN a v0.1 bundle whose `bundle/index.md` declares `okf_version: "0.1"`
- WHEN `openkos repair` runs
- THEN exactly one commit contains both the rewritten concepts and
  `bundle/index.md`'s `okf_version: "0.2"` — no intermediate commit ever
  declares v0.2 while a concept remains v0.1-shaped

### Requirement: `repair` OKF Migration Is Idempotent

A second `openkos repair` run against an already-migrated bundle MUST
report nothing to migrate and MUST write nothing, for the OKF migration
exactly as for `repair`'s existing merge-ledger migration.

#### Scenario: Re-running repair after a successful migration is a no-op

- GIVEN a bundle `openkos repair` just migrated to v0.2 successfully
- WHEN `openkos repair` runs again immediately
- THEN it reports nothing to migrate, writes nothing, and creates no commit

### Requirement: `repair` Preserves Hand-Authored `# Citations` Content, Never Converts It

WHEN a Source or derived concept carries a NON-EMPTY, hand-authored
`# Citations` body section, `repair`'s OKF migration MUST leave that
section's content in place — untouched and unconverted into `sources`
entries — and MUST report it in the migration's output. Only the engine's
OWN bare, empty `# Citations` heading (with no content under it) MUST be
removed.

#### Scenario: A non-empty Citations section survives migration

- GIVEN a concept whose `# Citations` section carries hand-authored,
  non-empty content
- WHEN `openkos repair` migrates that concept
- THEN the `# Citations` section and its content are unchanged, and the
  migration's report names that concept as carrying a preserved legacy
  Citations section

#### Scenario: A bare empty Citations heading is removed

- GIVEN a concept whose `# Citations` heading has no content under it
- WHEN `openkos repair` migrates that concept
- THEN the heading is removed

### Requirement: `repair` OKF Migration Refuses Consistently With Existing Guards

The OKF migration MUST reuse `repair`'s existing safety scaffolding
unchanged: it MUST refuse under the workspace lock, MUST refuse on a torn
or pending write, MUST refuse with "nothing to migrate" when no v0.1-shaped
concept exists, and MUST print the existing git reset-point note (or its
existing no-reset-point statement) exactly as `repair`'s merge-ledger
migration does today.

#### Scenario: A workspace-locked bundle refuses the OKF migration

- GIVEN another process holds the workspace lock
- WHEN `openkos repair` attempts the OKF migration
- THEN it refuses, exits non-zero, and writes nothing

#### Scenario: A bundle with no v0.1-shaped concept refuses with nothing to migrate

- GIVEN a bundle where every concept is already v0.2-shaped
- WHEN `openkos repair` runs
- THEN it reports nothing to migrate and writes nothing

### Requirement: Merge, Repair, And Unmerge Leave No V0.1-Shaped Document

For a survivor and absorbed pair merged BEFORE `repair`'s OKF migration
runs, the sequence merge → `repair` → `unmerge` MUST leave the bundle with
no v0.1-shaped document: every restored document, including one restored
from a merge ledger's pre-migration snapshot, MUST end the sequence in v0.2
shape.

#### Scenario: A concept restored by unmerge after repair is not v0.1-shaped

- GIVEN a merge recorded before `openkos repair`'s OKF migration ran
- WHEN `repair` migrates the bundle and `unmerge` subsequently reverses that
  merge
- THEN neither the restored survivor nor the restored absorbed document
  carries a legacy `timestamp`, `status: active`, or a bare empty
  `# Citations` heading
