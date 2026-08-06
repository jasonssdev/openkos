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

### Requirement: Unextracted-Source Scan

`openkos lint` MUST flag any Source document whose frontmatter
`extraction_status` equals `failed` as an `unextracted` finding
(`LintFinding.kind`, joining `stale`/`orphan`/`dangling`). The other three
`extraction_status` values (`no-extractable-text`, `blocked-by-sensitivity`,
`no-concepts-found`) MUST NEVER produce this finding —
`blocked-by-sensitivity` in particular is a deliberate policy outcome, not
debt, and MUST NOT be reported as something to retry. The finding's detail
MUST name the literal retry command built from that Source's own `resource`
frontmatter value (`openkos ingest <resource>`), falling back to a generic
re-ingest hint only when `resource` is missing or empty. This scan MUST
reuse `LintDoc`'s existing single-pass `collect_docs` walk — no new bundle
walk — and MUST NOT change `lint`'s exit code: `lint`'s Non-Gating Exit
Contract already covers all existing kinds and MUST cover this one too.

#### Scenario: failed Source produces an unextracted finding

- GIVEN a Source document with `extraction_status: failed`
- WHEN `openkos lint` runs
- THEN it reports an `unextracted` finding for that Source

#### Scenario: blocked-by-sensitivity produces no finding

- GIVEN a Source document with `extraction_status: blocked-by-sensitivity`
- WHEN `openkos lint` runs
- THEN no `unextracted` finding is reported for that Source, and it appears
  in no retry prompt

#### Scenario: Detail names the exact retry command

- GIVEN a Source with `extraction_status: failed` and `resource: raw/foo.md`
- WHEN `openkos lint` runs
- THEN the finding's detail text names the command
  `openkos ingest raw/foo.md` verbatim

#### Scenario: lint exits 0 with unextracted findings present

- GIVEN a bundle containing at least one `unextracted` finding
- WHEN `openkos lint` runs
- THEN it reports the finding(s) and still exits 0

### Requirement: Below-Source Sensitivity Scan

`openkos lint` MUST flag any provenance descendant for which
`okf.combine_sensitivity(descendant_sensitivity, source_sensitivity)`
differs from the descendant's current value — the same test the sweep uses
to stage a write, so a missing, blank, or unrecognized `sensitivity` is
ranked fail-closed (ADR-0003) and is flagged — as a
`below-source-sensitivity` finding (`LintFinding.kind`, joining
`stale`/`orphan`/`dangling`/`unextracted`). The scan MUST reuse the SAME
closure algorithm and rank comparator the `sensitivity-backfill` verb uses
(`bundle.provenance.provenance_closure` plus `okf.combine_sensitivity`) and
MUST reuse `LintDoc`'s existing single-pass `collect_docs` walk; it MUST NOT
introduce a new bundle walk and MUST NOT render write-ready file content.
The finding's detail MUST name the descendant's current level, its Source's
level, and the Source's id. This scan MUST NOT change `lint`'s exit code:
the Non-Gating Exit Contract already covers all existing kinds and MUST
cover this one too.

#### Scenario: A descendant below its single Source is flagged

- GIVEN a Source with `sensitivity: confidential` and a provenance
  descendant citing only that Source with `sensitivity: public`
- WHEN `openkos lint` runs
- THEN it reports a `below-source-sensitivity` finding naming the
  descendant, its current level, the Source's level, and the Source's id

#### Scenario: A descendant at or above its Source's level produces no finding

- GIVEN a Source and a provenance descendant already at or above the
  Source's `sensitivity`
- WHEN `openkos lint` runs
- THEN no `below-source-sensitivity` finding is reported for that descendant

#### Scenario: A clean bundle reports no below-source findings

- GIVEN a bundle where every descendant's `sensitivity` already meets or
  exceeds its citing Source's level
- WHEN `openkos lint` runs
- THEN it reports zero `below-source-sensitivity` findings and still exits 0

#### Scenario: below-source-sensitivity findings do not change the exit contract

- GIVEN a bundle containing one or more `below-source-sensitivity` findings
- WHEN `openkos lint` runs
- THEN it reports the findings and still exits 0, and no bundle file is
  created, modified, or deleted

#### Scenario: A missing or dirty sensitivity under a Source is flagged fail-closed

- GIVEN a provenance descendant whose `sensitivity` is missing, blank, or
  not a recognized level, citing a single Source with `sensitivity: public`
- WHEN `openkos lint` runs
- THEN `okf.combine_sensitivity` ranks the dirty value fail-closed (ADR-0003)
  and a `below-source-sensitivity` finding is reported for that descendant

### Requirement: Multi-Source Uncovered-Descendant Scan

`openkos lint` MUST flag, as a distinct finding kind
(`multi-source-uncovered`), any doc with a non-empty `provenance` whose
cited ids all resolve to bundle concepts, which is a member of no
single-Source closure, and whose `sensitivity` sits strictly below the
high-water-mark of its cited concepts' levels. This category MUST be
reported separately from `below-source-sensitivity` findings — it names
descendants the `sensitivity-backfill` sweep cannot and will not raise, per
that verb's per-Source scan scope — and its detail MUST name the
descendant, its current level, and every cited concept id with that
concept's level, and MUST mark the finding as not covered by
`backfill-sensitivity`. A doc whose `provenance` cites two or more concepts
that all fall inside a single Source's closure MUST be reported as
`below-source-sensitivity`, not as `multi-source-uncovered`.

#### Scenario: A multi-source descendant below one of its Sources is flagged distinctly

- GIVEN a derived concept citing two Sources, neither of which lies inside
  the other's provenance closure, one at `sensitivity: public` and one at
  `sensitivity: confidential`, with the descendant itself at
  `sensitivity: public`
- WHEN `openkos lint` runs
- THEN it reports a `multi-source-uncovered` finding naming the descendant
  and both cited concept ids, distinct from any `below-source-sensitivity`
  finding

#### Scenario: A multi-source descendant already at the highest cited level produces no finding

- GIVEN a derived concept citing two Sources whose `sensitivity` is
  `public` and `private`, with the descendant already at `private`
- WHEN `openkos lint` runs
- THEN no `multi-source-uncovered` finding is reported for that descendant

#### Scenario: A descendant citing one Source plus a foreign derived concept is flagged as uncovered

- GIVEN a derived concept whose `provenance` cites one Source directly and
  a second concept that is itself derived from a different Source, with the
  descendant's `sensitivity` below the high-water-mark of both cited levels
- WHEN `openkos lint` runs
- THEN it reports a `multi-source-uncovered` finding naming the descendant,
  its current level, and both cited concept ids with their levels

#### Scenario: A descendant citing two concepts inside the same Source's closure is reported as below-source, not uncovered

- GIVEN a derived concept whose `provenance` cites two concepts that are
  both members of the same Source's closure, with the descendant's
  `sensitivity` below that Source's level
- WHEN `openkos lint` runs
- THEN it reports a `below-source-sensitivity` finding for that descendant,
  and no `multi-source-uncovered` finding is reported for it

### Requirement: Unbacked-Provenance-Claim Scan

`openkos lint` MUST flag, as a distinct finding kind
(`unbacked-provenance`), every `relations:` entry whose relation type is
engine-owned and whose target is absent from the SAME document's
`provenance:` list. The set of engine-owned types MUST be read from
`model/relations.py::ENGINE_OWNED_RELATION_TYPES` (today exactly
`derived_from`), never hard-coded in the check, so that a second
engine-derived type is covered without rewriting the scan. An
engine-owned entry whose target IS recorded in `provenance:` MUST NOT be
flagged, and the existence of the target in the bundle MUST NOT be tested
here — a target absent from the bundle is already reported as `dangling`,
and a claim can be unbacked while naming a document that exists.

The scan MUST be pure and deterministic: it MUST NOT call any model
backend, read any clock, or perform a bundle walk of its own, reusing
`LintDoc`'s existing single-pass `collect_docs` walk. The finding's detail
MUST name the citing document, the offending relation type, the offending
target, and the provenance the document actually records. `openkos lint`
MUST NOT delete, rewrite, or otherwise repair the offending `relations:`
entry, and the finding's detail MUST NOT name a command that would: a
human-accepted relation is removed only by an explicit human edit. This
scan MUST NOT change `lint`'s exit code: the Non-Gating Exit Contract
already covers all existing kinds and MUST cover this one too.

#### Scenario: A derived_from absent from the document's provenance is flagged

- GIVEN a concept whose `relations:` contains an entry typed `derived_from`
  targeting a concept that does not appear in that concept's `provenance:`
- WHEN `openkos lint` runs
- THEN it reports an `unbacked-provenance` finding naming the citing
  concept, the relation type, the target, and the provenance the document
  records

#### Scenario: A derived_from backed by the document's provenance produces no finding

- GIVEN a concept whose `relations:` contains an entry typed `derived_from`
  targeting an id that IS listed in that concept's `provenance:`
- WHEN `openkos lint` runs
- THEN no `unbacked-provenance` finding is reported for that entry

#### Scenario: A human-authored relation type is never a subject

- GIVEN a concept whose `relations:` contains only non-engine-owned entries
  (for example `related_to`) targeting ids absent from its `provenance:`
- WHEN `openkos lint` runs
- THEN no `unbacked-provenance` finding is reported for that concept

#### Scenario: An engine-owned claim on a document with no provenance is flagged

- GIVEN a concept with an engine-owned `relations:` entry and an absent or
  empty `provenance:` list
- WHEN `openkos lint` runs
- THEN it reports an `unbacked-provenance` finding for that entry

#### Scenario: The offending relation is reported, never repaired

- GIVEN a bundle containing at least one `unbacked-provenance` finding
- WHEN `openkos lint` runs
- THEN it reports the finding, still exits 0, and no bundle file is
  created, modified, or deleted
