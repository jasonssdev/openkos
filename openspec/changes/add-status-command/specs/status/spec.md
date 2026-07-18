# Status Specification

## Purpose

`openkos status` is the first read-only command: it reports what a bundle
currently contains — source/concept counts, recent activity, and anything
needing attention — without mutating any bundle file. It establishes the
bundle-reader precedent that `query`/`lint` will follow.

## Non-Goals

This spec does not define: lint checks (stale-stamp, orphan-page detection —
future `lint` command); `--json` or any structured output; non-zero exit on
findings or CI-gate behavior (findings are informational only).

## Requirements

### Requirement: Workspace Presence Check

`openkos status` MUST refuse to run outside an initialized workspace, using
the same shared `require_workspace` check `ingest` uses, and MUST NOT produce
a raw traceback.

#### Scenario: Run outside a workspace

- GIVEN a directory that is not an initialized OpenKOS workspace
- WHEN `openkos status` runs
- THEN it exits non-zero, prints a clear error to stderr, and prints no raw
  traceback

### Requirement: Disk-Scan Source and Concept Counts

`openkos status` MUST report counts of sources and concepts derived from a
fresh scan of `bundle/**/*.md` (excluding reserved filenames), not from
`index.md` alone. A file with frontmatter `type: Source` MUST be counted as a
source; every other non-reserved typed file MUST be counted as a concept.

#### Scenario: Healthy bundle with sources

- GIVEN an initialized workspace with N ingested sources and no concept files
- WHEN `openkos status` runs
- THEN it reports `sources: N` and `concepts: 0`, matching the disk scan

#### Scenario: Freshly initialized empty bundle

- GIVEN a freshly initialized workspace with no sources ingested yet
- WHEN `openkos status` runs
- THEN it reports `sources: 0` and `concepts: 0` with a sensible empty-state
  message, and exits 0

#### Scenario: Catalog drift — disk is the truth

- GIVEN a bundle where a source file exists on disk but is not reflected in
  `index.md` (e.g. after an interrupted `ingest`)
- WHEN `openkos status` runs
- THEN the reported counts include the on-disk file, even though `index.md`
  does not list it

### Requirement: Recent Activity from log.md

`openkos status` MUST report recent activity read from `bundle/log.md`,
newest-first.

#### Scenario: Healthy bundle shows recent activity

- GIVEN a workspace with existing dated entries in `log.md`
- WHEN `openkos status` runs
- THEN it reports the recent activity from `log.md`, newest entries first

#### Scenario: Empty log

- GIVEN a freshly initialized workspace with an empty or absent `log.md`
  activity section
- WHEN `openkos status` runs
- THEN it reports a sensible "no recent activity" state and exits 0

### Requirement: Needs-Attention via §9 Conformance

`openkos status` MUST surface OKF §9 conformance findings (unparseable
frontmatter, missing/empty `type`) by reusing `check_conformance`, under a
"needs attention" section. Findings MUST be informational: their presence
MUST NOT cause a non-zero exit.

#### Scenario: No conformance issues

- GIVEN a bundle where every non-reserved file passes `check_conformance`
- WHEN `openkos status` runs
- THEN it reports a "no issues" needs-attention line and exits 0

#### Scenario: Conformance violation is surfaced but non-fatal

- GIVEN a bundle containing a concept file with a missing `type` field
- WHEN `openkos status` runs
- THEN the violation is listed under "needs attention" and the command still
  exits 0

### Requirement: Read-Only and Human-Readable Only

`openkos status` MUST NOT write, modify, or delete any bundle file, and MUST
produce human-readable text output only; no `--json` or other structured
output mode is offered.

#### Scenario: No mutation on any run

- GIVEN any workspace state (empty, healthy, or with conformance findings)
- WHEN `openkos status` runs
- THEN no file under the workspace is created, modified, or deleted, and no
  `--json` flag is accepted
