# Proposal: okf-export — write a shareable, conformant OKF bundle that withholds what must not leave the device

MVP 5 (Interoperability), first deliverable, per `docs/roadmap.md` ("OKF
export first … sensitivity enforcement at the export boundary — confidential
objects excluded from exports and sharing"). No issue exists yet; one is
opened before the first implementation PR and referenced from there.

## Intent

`bundle/` is already a conformant OKF v0.2 bundle by construction, so a
reader can open it as-is. What it is not is *shareable*: it holds every
object at every sensitivity, and `docs/knowledge-object-model.md`
("Sensitivity and access boundaries") already promises that export is a
boundary — `public` is "safe to share, export, or publish", `private` is
"not exported or shared unless you explicitly choose to", and `confidential`
is "excluded from exports and sharing". Copying `bundle/` by hand breaks
that promise silently, and also ships pointers into the withheld part (body
links, `relations:`, `provenance:`, `sources:`, `index.md` and `log.md`
lines), so even a hand-pruned copy leaks titles and ids of what it left out.

`openkos export <target>` closes the last sensitivity boundary the KOM names
as open ("Export/import is the remaining boundary (MVP 5)"): it writes a new
OKF bundle containing only the objects the boundary admits, with every
structured pointer into a withheld object removed, and it proves the output
conformant (§11) and leak-free before publishing it. It is also what first
makes OpenKOS's conformance claim testable by somebody else.

## What the bundle already satisfies vs what export must do

| Concern | Already true of `bundle/` | Export must |
|---|---|---|
| §11 rule 1–2 (frontmatter, non-empty `type`) | yes, by construction; `okf.check_conformance` checks it | re-check the OUTPUT tree, since export rewrites documents |
| §11 rule 3 (`index.md`, `log.md` shape) | yes | re-check after filtering both files |
| Concept ID = path minus `.md` | yes | preserve paths exactly (no renames), so ids are stable across the boundary |
| `raw/` outside the bundle | yes | nothing — `raw/` is never read |
| `bundle/.state/` sidecars, any dot-directory (ADR-0019) | not concepts | exclude; they are engine state (merge ledger snapshots can hold higher-sensitivity bodies) |
| §4.1 extension keys | legal | keep them, except the few that are machine-local or would carry withheld data (design D7) |
| `sources` as a projection of `provenance` (§5.1) | yes | filter `provenance` to exported ids, then RE-project `sources` from it — never edit `sources` directly |
| deprecated-status export (`status: deprecated` + marker) | kept consistent by `repair` | project it in memory over the whole bundle so drift on disk never reaches an external reader |
| Sensitivity | a label on every object | enforce it: allowed set, fail-closed |
| Pointers into withheld objects | n/a (nothing is withheld in place) | remove every structured pointer; decide what happens to body links (open question 1) |

## Scope

### In Scope

- A new read-only verb `openkos export <target-dir>`, with
  `--include-private` (explicit opt-in to export `private` objects) and
  `--auto` (skip the confirm gate), following the preview / confirm / exit
  code conventions of `docs/cli.md` and the TTY output convention of
  ADR-0042.
- A fail-closed ALLOWED-set predicate for the export boundary in
  `sensitivity.py`, sibling to `disclosable_concept_ids` (ADR-0028): `public`
  by default; `public` + `private` only with `--include-private`;
  `confidential`, absent, blank and unrecognized labels never, with no
  opt-in.
- Frontmatter transformation inside the OKF seam (`model/okf.py`):
  `relations:` and `provenance:` filtered to exported ids, `sources:`
  re-projected, deprecated-status projected, machine-local keys stripped.
- Removal of structured pointers into withheld objects from bodies (per open
  question 1), from `index.md`, and from `log.md` (per open question 3).
- Two self-checks on the staged output before it is published: §11
  conformance (`okf.check_conformance`) and a leak check that no exported byte
  contains a pointer to a withheld id.
- Stage-then-publish: the output is built in a sibling staging directory and
  renamed into place only after both checks pass, so a refused export leaves
  nothing behind.
- A snapshot-consistency check: the verb takes no lock (it never writes the
  workspace) and instead refuses with exit 3 if any input changed while it
  ran.
- `docs/cli.md` entry for the verb.

### Out of Scope

- **OKF import** (the roadmap's second deliverable; cross-bundle entity
  resolution, its own arc).
- Tarball / zip output: OKF §3 permits both, and `tar`/`zip` over the
  exported directory produce them; a built-in archive flag can follow.
- Shipping `raw/` material (open question 4 records the choice; this change
  ships none).
- Exporting `confidential` objects under any flag.
- Writing anything to the workspace: no `log.md` entry, no commit, no lock.
- Incremental / re-export into an existing export directory.
- A stable public Python API (MVP 6, ADR-0039): export is built on a narrow
  `application/` service only.
- Redacting prose: text that is not a link stays as written (ADR-0028's
  "exclusion, not redaction" rule), unless open question 1 is answered
  otherwise for link labels.

## Approach

Build the boundary before the verb. First the allowed-set predicate (the
only thing that decides what leaves), then the pure document transforms in
the OKF seam and the link/catalog filters, then the `application/` service
that walks once, plans, stages, self-checks, and publishes, then the CLI
verb. Each phase is a work-unit-sized PR with its own tests; see
`tasks.md`. Design decisions are in `design.md`.

## Affected Areas

`src/openkos/sensitivity.py` (new allowed-set predicate),
`src/openkos/model/okf.py` (export frontmatter transform),
`src/openkos/bundle/links.py`, `bundle/index.py`, `bundle/log.py` (pure
filters), new `src/openkos/application/export_service.py`,
`src/openkos/cli/main.py` (verb + read-only classification), tests under
`tests/unit/` (including an e2e over `examples/good-life-demo/`),
`docs/cli.md`.

## Principles Impact

- **Adopt OKF**: output is a plain OKF v0.2 bundle; nothing is added that
  OKF does not already permit (§4.1 extensions, §6.1 untyped links).
- **Sensitivity across boundaries**: implements the export boundary the KOM
  already defines; `confidential` never leaves, with no flag.
- **OKF adapter is one seam**: every frontmatter decision lives in
  `model/okf.py`.
- **Reconstructible**: export reads canonical files only; it consults no
  derived store.
- **Immutable sources**: `raw/` is never read or written.
- **Human curates**: the preview names what leaves and what is withheld
  before anything is written; `private` leaves only on an explicit flag.

## Risks

- **A pointer channel the filters miss** (a reference-style link, a link in
  an unexpected key) leaks a withheld id. Mitigated by the leak check, which
  scans every staged byte for every withheld id's path forms and refuses the
  whole export rather than publishing a partial leak.
- **A torn snapshot** if a writer runs during export. Mitigated by the
  input re-read and exit-3 refusal.
- **Default surprises a new user**: a stock workspace labels everything
  `private`, so a bare `openkos export` exports nothing. Mitigated by
  refusing with a message that names the withheld counts and
  `--include-private`, instead of writing an empty bundle.
- **Prose names withheld objects** (a sentence mentioning a confidential
  person). Not detectable structurally; the preview says prose is not
  redacted, consistent with ADR-0028.

## Rollback Plan

The verb writes only to a new directory outside the workspace and touches
no canonical file, so reverting its PRs removes the capability with no data
migration. An export already shared cannot be recalled; that is why the
default is the narrowest boundary and the self-checks refuse rather than
warn.

## Dependencies

None beyond shipped code: `okf.check_conformance`,
`okf.project_sources`, `okf.apply_deprecation_export`,
`lifecycle.superseded_from_metadata`, `bundle/links.py`'s link scanner,
`bundle/index.py::remove_index_entry`, `bundle/log.py::remove_log_entry`.

## Success Criteria

- Exporting `examples/good-life-demo` with `--include-private` yields exactly
  `concepts/epicureanism` and `sources/notes-on-the-enchiridion-2026-07-05`
  plus `index.md` (and `log.md` per Q3), passes `okf.check_conformance`, and
  no output byte contains `stoicism`, `maria-salazar`,
  `frame-the-essay-on-the-dichotomy-of-control` or
  `call-with-maria-2026-07-14` as a link target, relation target,
  provenance id, or `sources` entry.
- Without `--include-private` the same workspace refuses with exit 1 and
  writes nothing.
- Two exports of an unchanged workspace are byte-identical.

## Open Questions (product decisions — not decided here)

### Q1. A link in an exported body that points at a withheld object

Example: `concepts/epicureanism` (private) has a Related line
`- [Stoicism](/concepts/stoicism.md) — contrasted with …`, and Stoicism is
confidential.

- **A. Keep the body verbatim.** Same rule as MCP `get` (ADR-0028: prose is
  not redacted). OKF §6.1 tolerates the broken link. Leaks the withheld
  object's title (the label) and its id (the path) — and ids are title slugs.
- **B. Unlink, keep the label** (`Stoicism — contrasted with …`). No dangling
  pointer; the title still leaks through the label.
- **C. Unlink and replace the label with a fixed marker**
  (`[withheld] — contrasted with …`). No id, no title; the surrounding prose
  stays. Changes the reading of the sentence and, unlike ADR-0028, edits the
  body.
- **D. Withhold the linking object too.** Nothing edited, but one
  confidential link withholds the whole linker, transitively; most private
  objects in a real workspace would vanish.

**Recommendation: C.** ADR-0028 already classes titles and ids as
structured channels to filter, and a link label written by the engine is
the target's title — it is that channel, embedded in the body. Prose
outside links stays verbatim, so the "exclusion, not redaction" rule still
holds for text. If chosen, record the departure from ADR-0028 for the export
boundary in ADR-0048.

### Q2. An object a human downgraded below its sources' sensitivity

ADR-0008 lets `set-sensitivity` lower a label deliberately; the KOM states
the high-water mark rule. A `private` object whose provenance includes a
`confidential` Source can exist either by that deliberate downgrade or by
stale machine labelling (lint's `below-source-sensitivity`).

- **A. Own label only.** Same predicate as retrieval and MCP; honors
  ADR-0008. A stale machine label below its source leaks.
- **B. Recompute the high-water mark over the provenance closure.** Strict;
  silently overrides every deliberate downgrade.
- **C. Own label, but withhold an object below its provenance high-water
  mark unless `--allow-below-source`** is given, and list those objects in
  the preview.

**Recommendation: C.** It keeps one predicate for the label and honors the
human downgrade, but puts friction exactly where the machine and the human
may disagree — the same placement ADR-0008 uses for `--allow-downgrade`.

### Q3. `log.md` in the export

The workspace log records `raw/` file names, sensitivity transitions,
forgotten-object tombstones, and lines that link several objects at once.

- **A. Filter it**: keep a line only if every link and `(id: …)` anchor on it
  resolves to an exported object. Keeps history; prose on a kept line
  (raw file names, "derives from a confidential source") still leaves.
- **B. Omit it.** OKF makes `log.md` optional (§9); history stays in the
  workspace's git.
- **C. Write a fresh `log.md`** with one `**Export**` entry dated the export
  day.

**Recommendation: C.** Export is a publication, not a mirror; the workspace
log is curation history whose prose cannot be filtered fail-closed. A fresh
one-entry log keeps the bundle self-describing at no leak risk.

### Q4. `raw/` material behind exported Sources

An exported Source carries `resource: raw/<file>`, which does not resolve in
the export.

- **A. Ship nothing**; leave `resource` as written (OKF §6.1/§6.2 tolerate
  an unresolvable path).
- **B. Copy the raw file under `references/raw/`** (OKF §6.3 convention) and
  rewrite `resource` to point there. Self-contained, but ships the
  unprocessed original — often far more than the compiled Source says.
- **C. Ship nothing and drop `resource`** from exported Sources.

**Recommendation: A** for this change, with B as a later opt-in flag. The
compiled bundle is what OKF consumers read; the raw original is the most
sensitive artifact in the workspace and should never leave by default.
