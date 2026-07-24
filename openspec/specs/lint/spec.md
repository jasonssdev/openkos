# Lint Specification

## Purpose

`openkos lint` is the second read-only bundle-reader command (after
`status`): a purely mechanical, read-only health check that flags two
freshness signals — stale inline stamps and orphan pages — without mutating
any bundle file.

## Non-Goals

This spec does not define: CI-gating, non-zero exit on findings, or severity
thresholds (findings are informational only, mirroring `status`); error vs.
warning tiers (flat warning-level in MVP-1); `--json` or any structured
output; volatility classification via the `freshness` field remains out of
scope — `freshness` stays a binary snapshot/non-snapshot skip flag,
orthogonal to volatility; volatility classification is instead read from
the concept's `volatility` field and per-type registry default (see
`concept-volatility`), applied only to resolve each concept's stale-stamp
window; conformance checking (`check_conformance` / OKF §9 stays a
separate vocabulary).

## Requirements

### Requirement: Workspace Presence Check

`openkos lint` MUST refuse to run outside an initialized workspace, using
the same shared `require_workspace` check `ingest`/`status` use, and MUST
NOT produce a raw traceback.

#### Scenario: Run outside a workspace

- GIVEN a directory that is not an initialized OpenKOS workspace
- WHEN `openkos lint` runs
- THEN it exits non-zero, prints a clear error to stderr, and prints no raw
  traceback

### Requirement: Stale-Stamp Scan

`openkos lint` MUST scan concept bodies for inline `(as of YYYY-MM-DD)`
stamps and flag any stamp older than that concept's volatility-resolved
stale window (per `concept-volatility` precedence: per-concept `volatility`
override → per-type default → global `freshness_window` fallback, default
`7d`) as a stale-stamp finding. `static`-tier concepts MUST NEVER be
flagged regardless of stamp age. The scan MUST read only inline body text
for the stamp itself, never `freshness` for age, EXCEPT that the scan MUST
skip entirely any concept whose `freshness` field is `snapshot` — such
concepts (as produced by `openkos ingest`) embed verbatim source text that
MAY coincidentally contain an `(as of ...)`-shaped string, and that text is
not a maintained freshness stamp, independent of resolved volatility tier.

#### Scenario: Stale stamp is flagged

- GIVEN a non-snapshot concept body containing `(as of YYYY-MM-DD)` older
  than the concept's volatility-resolved stale window
- WHEN `openkos lint` runs
- THEN the concept is reported as a stale-stamp finding

#### Scenario: Fresh stamp is not flagged

- GIVEN a non-snapshot concept body containing `(as of YYYY-MM-DD)` within
  the concept's volatility-resolved stale window
- WHEN `openkos lint` runs
- THEN the concept is NOT reported as a stale-stamp finding

#### Scenario: static-tier concept is never flagged

- GIVEN a `static`-tier concept (by `volatility` override or per-type
  default) with an arbitrarily old `(as of YYYY-MM-DD)` stamp
- WHEN `openkos lint` runs
- THEN the concept is NOT reported as a stale-stamp finding

#### Scenario: Per-concept override wins over type default

- GIVEN a `Procedure` concept (whose type default is `volatile`) with
  `volatility: static` and an old `(as of YYYY-MM-DD)` stamp
- WHEN `openkos lint` runs
- THEN the concept is NOT reported as a stale-stamp finding

#### Scenario: Type default wins over global fallback

- GIVEN a `slow`-tier concept whose resolved window is longer than the
  global `freshness_window` fallback, with a stamp older than the fallback
  but within the `slow`-tier window
- WHEN `openkos lint` runs
- THEN the concept is NOT reported as a stale-stamp finding

#### Scenario: Pure-ingest bundle produces zero stale findings

- GIVEN a bundle containing only `freshness: snapshot` Source concepts
  produced by `openkos ingest`
- WHEN `openkos lint` runs
- THEN it reports zero stale-stamp findings, regardless of any
  `(as of ...)`-shaped text embedded in their bodies or resolved
  volatility tier

#### Scenario: Snapshot concept with an embedded stamp-shaped string is not flagged

- GIVEN a `freshness: snapshot` Source concept whose embedded verbatim
  content contains text matching `(as of YYYY-MM-DD)`
- WHEN `openkos lint` runs
- THEN no stale-stamp finding is reported for that concept, regardless of
  its resolved volatility tier

#### Scenario: Unresolvable volatility still degrades to the global fallback

- GIVEN a concept with an unknown `type` AND an invalid `volatility` value
- WHEN `openkos lint` runs
- THEN its stale window resolves to the global `freshness_window` fallback
  and the scan never raises

### Requirement: Orphan-Page Scan

`openkos lint` MUST flag any concept file not referenced by a markdown
link from `index.md` or from another concept's body as an orphan-page
finding. The scan MUST be a flat link scan (no dependency graph).

#### Scenario: Unreferenced concept is flagged as orphan

- GIVEN a concept file with no inbound markdown link from `index.md` or
  any other concept's body
- WHEN `openkos lint` runs
- THEN the concept is reported as an orphan-page finding

#### Scenario: Concept linked from index.md is not an orphan

- GIVEN a concept file referenced by a markdown link in `index.md`
- WHEN `openkos lint` runs
- THEN the concept is NOT reported as an orphan-page finding

#### Scenario: Concept linked from another concept's body is not an orphan

- GIVEN a concept file referenced by a markdown link inside another
  concept's body
- WHEN `openkos lint` runs
- THEN the concept is NOT reported as an orphan-page finding

### Requirement: Dangling-Reference Scan

`openkos lint` MUST flag any concept document whose outbound reference names
a concept id absent from disk as a dangling-reference finding. An outbound
reference is either (a) a `relations:` frontmatter target id, or (b) a body
markdown bundle link resolved via the same `normalize_link` resolution
`lint`'s orphan-page scan already uses. The check MUST run beside
`check_orphans` (a new `check_dangling_targets(docs)`), scan every
non-reserved bundle document, and report each finding as the referring
document's id/path plus the missing target id. The scan MUST NOT write,
modify, or delete any bundle file, and MUST NOT gate the command's exit
code (informational only, per the Non-Gating Exit Contract).

#### Scenario: relations: target absent from disk is flagged

- GIVEN a concept document with a `relations:` entry naming a target concept
  id that has no corresponding file on disk
- WHEN `openkos lint` runs
- THEN it reports a dangling-reference finding naming the referring document
  and the missing target id

#### Scenario: Body markdown bundle link to an absent id is flagged

- GIVEN a concept document whose body contains a markdown link that
  `normalize_link` resolves to a concept id absent from disk
- WHEN `openkos lint` runs
- THEN it reports a dangling-reference finding naming the referring document
  and the missing target id

#### Scenario: Reference to an existing concept is not flagged

- GIVEN a concept document whose `relations:` target and body links all
  resolve to concept ids present on disk
- WHEN `openkos lint` runs
- THEN no dangling-reference finding is reported for that document

#### Scenario: Purge leaves a referring document detectably dangling

- GIVEN a concept document referencing concept `<id>` via `relations:` or a
  body link, and `<id>` is then removed by `openkos purge <id> --force`
- WHEN `openkos lint` runs afterward
- THEN it reports a dangling-reference finding for the referring document
  naming `<id>` as the missing target

#### Scenario: Dangling-reference findings do not change the exit contract

- GIVEN a bundle containing one or more dangling-reference findings
- WHEN `openkos lint` runs
- THEN it reports the findings and still exits 0, and no bundle file is
  created, modified, or deleted

### Requirement: Non-Gating Exit Contract

`openkos lint` MUST exit 0 on any successful run, whether the bundle is
clean or contains findings. `lint` MUST NOT be a CI gate in MVP-1: a
non-zero exit occurs ONLY when the workspace cannot be read.

#### Scenario: Empty or fresh bundle has no findings

- GIVEN an initialized workspace with no stale stamps or orphan concepts
- WHEN `openkos lint` runs
- THEN it reports a sensible empty-state message with no findings and
  exits 0

#### Scenario: Bundle with findings still exits 0

- GIVEN a bundle containing at least one stale-stamp or orphan-page
  finding
- WHEN `openkos lint` runs
- THEN it reports the findings and exits 0

### Requirement: Read-Only and Human-Readable Only

`openkos lint` MUST NOT write, modify, or delete any bundle file, and MUST
produce human-readable text output only; no `--json` or other structured
output mode is offered. Findings MUST be flat warning-level (no
error/warning tiers).

#### Scenario: No mutation on any run

- GIVEN any workspace state (empty, clean, or with findings)
- WHEN `openkos lint` runs
- THEN no file under the workspace is created, modified, or deleted, and
  no `--json` flag is accepted
