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
output; volatility classification via the `freshness` field (lint never
reads it); conformance checking (`check_conformance` / OKF §9 stays a
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
stamps and flag any stamp older than the configured `freshness_window`
(default `7d`) as a stale-stamp finding. The scan MUST read only inline
body text, never the `freshness` field. A `freshness: snapshot` concept
carries no `(as of ...)` stamp by design, so a bundle whose concepts are
exclusively of that kind MUST produce zero stale-stamp findings.

#### Scenario: Stale stamp is flagged

- GIVEN a concept body containing `(as of YYYY-MM-DD)` older than the
  configured `freshness_window`
- WHEN `openkos lint` runs
- THEN the concept is reported as a stale-stamp finding

#### Scenario: Fresh stamp is not flagged

- GIVEN a concept body containing `(as of YYYY-MM-DD)` within the
  configured `freshness_window`
- WHEN `openkos lint` runs
- THEN the concept is NOT reported as a stale-stamp finding

#### Scenario: Pure-ingest bundle produces zero stale findings

- GIVEN a bundle containing only `freshness: snapshot` Source concepts
  produced by `openkos ingest`, none of which carry an `(as of ...)` stamp
- WHEN `openkos lint` runs
- THEN it reports zero stale-stamp findings

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
