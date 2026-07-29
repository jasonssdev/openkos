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

### Requirement: Needs-Attention Surfaces Dangling References

`openkos status` MUST fold `lint`'s dangling-reference findings
(`check_dangling_targets`) into its "needs attention" section, alongside
§9 conformance findings. Each surfaced entry MUST name the referring
document and the missing target id. Findings MUST be informational: their
presence MUST NOT cause a non-zero exit.

#### Scenario: Dangling reference is surfaced under needs attention

- GIVEN a bundle containing a concept document whose `relations:` target or
  body link resolves to a concept id absent from disk
- WHEN `openkos status` runs
- THEN the dangling reference is listed under "needs attention", naming the
  referring document and the missing target id, and the command still
  exits 0

#### Scenario: Purge-created dangling reference is detected by status

- GIVEN a concept document referencing concept `<id>`, and `<id>` is then
  removed by `openkos purge <id> --force` leaving the referring document's
  reference dangling
- WHEN `openkos status` runs afterward
- THEN the dangling reference is listed under "needs attention"

#### Scenario: No dangling references, no new needs-attention entries

- GIVEN a bundle where every `relations:` target and resolvable body link
  points to a concept id present on disk
- WHEN `openkos status` runs
- THEN no dangling-reference entry appears under "needs attention"

### Requirement: Needs-Attention Surfaces Pending Duplicate Groups

`openkos status` MUST consult `find_candidates` over the bundle (with the
default `include_deprecated=False`, offering no `--include-deprecated` flag)
and fold exact-title-match groups into "needs attention". Only exact-title
matches count toward this requirement; near-match groups MUST NOT cause
`status` to depart from `Nothing needs attention.` The surfaced line MUST
report the exact-title group count with correct singular/plural wording,
MUST name `openkos duplicates` as the next step, and MUST NOT use the words
`HIGH`, `LOW`, `exact`, or `near`, nor phrase the count as a total (the
count intentionally differs from — and is normally lower than — the count
`openkos duplicates` itself reports, once near-match groups exist). This
check MUST remain read-only and informational: its presence MUST NOT cause
a non-zero exit.

#### Scenario: No duplicate groups

- GIVEN a bundle with no candidate duplicate groups of any tier
- WHEN `openkos status` runs
- THEN no duplicate-groups entry appears under "needs attention" and the
  command still exits 0

#### Scenario: Exact-title duplicate groups are surfaced

- GIVEN a bundle containing two same-type documents sharing a normalized
  title (an exact-title-match group)
- WHEN `openkos status` runs
- THEN the group is listed under "needs attention" with the correct
  singular/plural count wording, naming `openkos duplicates` as the next
  step, without printing `HIGH`, `LOW`, `exact`, or `near`, and the command
  still exits 0 without printing `Nothing needs attention.`

#### Scenario: Only near-match groups still means nothing needs attention

- GIVEN a bundle whose only candidate duplicate groups are near-title
  matches (no exact-title-match group exists)
- WHEN `openkos status` runs
- THEN no duplicate-groups entry appears under "needs attention", the
  command still prints `Nothing needs attention.`, and it exits 0

#### Scenario: Deprecated-only duplicate group is excluded by default

- GIVEN a bundle whose only exact-title-match duplicate group consists
  entirely of deprecated concepts
- WHEN `openkos status` runs
- THEN no duplicate-groups entry appears under "needs attention" for that
  group, and the command still exits 0

### Requirement: Needs-Attention Surfaces Missing Vector Index

`openkos status` MUST report, under "needs attention", whether the
workspace's `vectors.db` (`layout.vectors_db_path`) is absent from disk.
This check MUST be informational only: its presence MUST NOT cause a
non-zero exit, and `status` MUST NOT attempt to rebuild or re-embed the
index itself.

#### Scenario: Missing vectors.db is surfaced

- GIVEN a workspace whose `.openkos/vectors.db` file is absent (e.g. after
  `openkos purge`)
- WHEN `openkos status` runs
- THEN it lists the missing vector index under "needs attention" and still
  exits 0

#### Scenario: Present vectors.db produces no vector-index entry

- GIVEN a workspace whose `.openkos/vectors.db` file exists on disk
- WHEN `openkos status` runs
- THEN no missing-vector-index entry appears under "needs attention"

### Requirement: Read-Only and Human-Readable Only

`openkos status` MUST NOT write, modify, or delete any bundle file, and MUST
produce human-readable text output only; no `--json` or other structured
output mode is offered.

#### Scenario: No mutation on any run

- GIVEN any workspace state (empty, healthy, or with conformance findings)
- WHEN `openkos status` runs
- THEN no file under the workspace is created, modified, or deleted, and no
  `--json` flag is accepted

### Requirement: Needs-Attention Reports Concept-to-Concept Edge State

`openkos status` MUST report, under "needs attention" (or an adjacent
informational line), the count of concept-to-concept edges in the graph
projection and the count of those that are typed, using three mutually
exclusive states: (1) the graph has zero concept-to-concept edges at all;
(2) concept-to-concept edges exist (typed and/or candidate); (3) candidate
edges are not computable yet because embeddings are missing (`vectors.db`
absent or empty). State 3 MUST be reported distinctly from state 1 — an
absent/empty `vectors.db` MUST NOT be reported using the same message as a
graph that genuinely has zero edges. This check MUST remain read-only and
informational: its presence MUST NOT cause a non-zero exit.

#### Scenario: Empty graph reports no concept relationships

- GIVEN a bundle whose graph projection has zero concept-to-concept edges,
  and `vectors.db` is present and populated
- WHEN `openkos status` runs
- THEN it reports "no concept relationships yet" (state 1), distinct from
  the embeddings-missing message, and exits 0

#### Scenario: Edges present reports counts

- GIVEN a bundle whose graph projection has concept-to-concept edges, some
  typed
- WHEN `openkos status` runs
- THEN it reports the total edge count and the typed subset count, and
  exits 0

#### Scenario: Missing embeddings reports a distinct not-computable state

- GIVEN a bundle whose `vectors.db` is absent or empty, so candidate edges
  cannot be computed
- WHEN `openkos status` runs
- THEN it reports that candidate edges are not computable yet (state 3),
  using a message distinguishable from "no concept relationships yet"
  (state 1), and exits 0

### Requirement: Needs-Attention Surfaces Unextracted Sources

`openkos status` MUST fold `lint`'s `unextracted` findings into its "needs
attention" section, naming the same retry command `lint` computes. This
requirement is deliberately spec-level, not an implementation detail: `status`
already runs four bundle walks (`main.py` docstring, consolidation tracked
separately under #195) and already folds `lint_check.collect_docs()`'s
dangling-reference findings into `needs_attention` without a fifth walk
(precedent: #216, where a repeated compute-then-discard walk was the bug).
`status` MUST consume the SAME in-memory `docs` list from the `collect_docs()`
call it already makes — it MUST NOT perform a second `collect_docs()` call or
any new `rglob`. Only `failed`-sourced `unextracted` findings reach
`needs_attention`; `status` remains read-only and MUST exit 0 regardless of
findings.

#### Scenario: Unextracted source surfaced under needs attention

- GIVEN a bundle containing a Source with `extraction_status: failed`
- WHEN `openkos status` runs
- THEN the retry command for that Source is listed under "needs attention",
  and the command still exits 0

#### Scenario: blocked-by-sensitivity never appears in the retry prompt

- GIVEN a bundle containing only a Source with
  `extraction_status: blocked-by-sensitivity`
- WHEN `openkos status` runs
- THEN no unextracted-source entry appears under "needs attention" for that
  Source, and it appears in no retry prompt

#### Scenario: No new bundle walk is introduced

- GIVEN `status` already calls `lint_check.collect_docs()` once for dangling
  findings
- WHEN the unextracted-source check also runs
- THEN it reuses that same in-memory `docs` list and `status` still performs
  no more bundle walks than before this change
