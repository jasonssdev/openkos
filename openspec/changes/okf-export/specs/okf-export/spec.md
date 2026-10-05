# OKF Export Specification

## Purpose

`openkos export <target>` writes a new, standalone OKF v0.2 bundle holding
only the objects the export sensitivity boundary admits, with every
structured pointer into a withheld object removed. It is the export
boundary `docs/knowledge-object-model.md` names: `public` objects leave by
default, `private` objects leave only on an explicit opt-in, and
`confidential` objects never leave. This capability owns the boundary
predicate, the transformation of exported documents, the treatment of
pointers into withheld objects, the two self-checks on the output, and the
verb's surface. Import is a separate capability.

## Non-Goals

This spec does not define: OKF import; tarball or zip output; copying `raw/`
material; any opt-in that exports a `confidential` object; any write to the
workspace (no `log.md` entry, no commit, no lock); re-exporting into an
existing export; redaction of prose that is not a link; or a stable public
Python API (ADR-0039).

## Requirements

### Requirement: The Export Boundary Is A Fail-Closed Allowed Set

The set of concept ids that MAY be exported MUST be computed as an ALLOWED
set in one walk of the bundle, so that an id the walk did not reach is
withheld by construction. A concept is allowed only when its document reads
and parses, carries a non-empty `type`, carries no `ingest_pending` marker,
and its own `sensitivity` is `public`, or is `private` and the run was given
`--include-private`. A `confidential`, absent, blank, non-string or
unrecognized `sensitivity` MUST be withheld under every flag. A symlinked
document MUST be withheld as unreadable. The predicate MUST live in
`sensitivity.py` beside the other boundary predicates, MUST take only the
documents' metadata, `include_private` and `allow_below_source`, and MUST
NOT accept any parameter that admits `confidential`.

An otherwise-allowed object whose own label ranks below the highest label
among its provenance ancestors (transitively, through every ancestor that is
a concept document in the bundle) MUST be withheld unless the run is given
`--allow-below-source` (ADR-0048). For this test an ancestor that cannot be
read, or whose label is missing or unrecognized, ranks as `confidential`; a
provenance entry under `raw/` or naming no document in the bundle
contributes nothing; an object whose `provenance` is present but malformed
counts as below its sources. Every object this rule withholds or admits
MUST be listed by id in the preview.

#### Scenario: Private objects stay home by default

- GIVEN a bundle whose concepts are labelled `public` and `private`
- WHEN `openkos export out/` runs without `--include-private`
- THEN only the `public` concepts are in `out/`

#### Scenario: Private objects leave on the explicit opt-in

- GIVEN the same bundle
- WHEN `openkos export out/ --include-private` runs
- THEN both the `public` and the `private` concepts are in `out/`

#### Scenario: Confidential never leaves

- GIVEN a concept labelled `confidential`
- WHEN `openkos export out/ --include-private` runs
- THEN that concept is not in `out/`, and no flag of the verb changes this

#### Scenario: An unlabelled object is withheld

- GIVEN a concept with no `sensitivity` key
- WHEN `openkos export out/ --include-private` runs
- THEN that concept is not in `out/` and the report counts it as withheld
  for a missing label

#### Scenario: An interrupted ingest is withheld

- GIVEN a Source carrying `ingest_pending: true`
- WHEN `openkos export out/ --include-private` runs
- THEN that Source is not in `out/` and the report counts it as withheld
  for an incomplete ingest

#### Scenario: An object labelled below its source is withheld by default

- GIVEN `concepts/a` labelled `private` with `provenance: [sources/s]`, and
  `sources/s` labelled `confidential`
- WHEN `openkos export out/ --include-private` runs
- THEN `concepts/a` is not in `out/`, and the preview names it as below its
  sources

#### Scenario: The human downgrade is honored on the explicit flag

- GIVEN the same bundle
- WHEN `openkos export out/ --include-private --allow-below-source` runs
- THEN `concepts/a` is in `out/`, `sources/s` is not, and `concepts/a`
  carries no pointer to `sources/s`

### Requirement: Exported Documents Keep Their Concept IDs

Every exported concept MUST be written at the same bundle-relative path it
has in `bundle/`, so its Concept ID (OKF §2) is identical on both sides of
the boundary. Only `.md` concept documents and the bundle-root reserved
files `index.md` and `log.md` MUST be written (a reserved file in a
subdirectory is not exported); `bundle/.state/`, every other
dot-directory, every non-`.md` file, and `raw/` MUST NOT be read into the
output.

#### Scenario: Paths are preserved

- GIVEN `concepts/epicureanism.md` is allowed
- WHEN the export runs
- THEN `out/concepts/epicureanism.md` exists and no other path holds it

#### Scenario: Engine state is never exported

- GIVEN a bundle with a merge ledger under `bundle/.state/ledger/`
- WHEN the export runs
- THEN `out/` contains no `.state` directory and no dot-directory at all

### Requirement: Exported Frontmatter Carries No Pointer Into A Withheld Object

For every exported concept, the export MUST rewrite its frontmatter through
one pure function in `model/okf.py` that: removes every `relations:` entry
whose target is not exported, removing the key when none remain; removes
every `provenance:` id that is not exported, removing the key when none
remain; and recomputes `sources:` from the filtered `provenance:` with
`okf.project_sources`, never by editing `sources:` directly. It MUST remove
the machine-local or engine-internal keys `origin_key` and `merged_from`.
Every other frontmatter key, including OpenKOS extension keys permitted by
OKF §4.1, MUST be kept with its value unchanged. A document whose
frontmatter and body need no change MUST be written byte-for-byte as read.

#### Scenario: A relation into a withheld object is removed

- GIVEN exported `a` with `relations: [{target: b, type: related_to},
  {target: c, type: related_to}]`, and `c` is withheld
- WHEN the export runs
- THEN `out/a.md`'s `relations:` holds only the entry for `b`

#### Scenario: `sources` follows the filtered provenance

- GIVEN exported `a` with `provenance: [sources/s1, sources/s2]`, and
  `sources/s2` is withheld
- WHEN the export runs
- THEN `out/a.md` has `provenance: [sources/s1]` and its `sources:` lists
  only the entry projected from `sources/s1`

#### Scenario: A machine-local key is stripped

- GIVEN an exported Source carrying `origin_key`
- WHEN the export runs
- THEN `out/`'s copy of that Source carries no `origin_key`, and every other
  key is unchanged

#### Scenario: An untouched document is copied byte-for-byte

- GIVEN an exported concept with no pointer into a withheld object and no
  stripped key
- WHEN the export runs
- THEN its bytes in `out/` equal its bytes in `bundle/`

### Requirement: The Deprecated-Status Export Is Projected Over The Whole Bundle

The export MUST compute the superseded set over every concept document in
`bundle/`, withheld ones included, and apply the deprecated-status
projection (`deprecated-status-export`) to each exported concept's
frontmatter in memory, so an exported concept superseded by any concept,
exported or not, reads `status: deprecated` with its marker. When the walk
is incomplete, the export MUST NOT withdraw an export on that basis, per
"Withdrawal Requires A Complete Edge Walk". A provenance-orphan
deprecation MUST NOT be exported, per "A Provenance-Derived Deprecation Is
Not Exported". The export MUST NOT write the projection back to `bundle/`;
it MUST report how many exported documents differed from their on-disk
projection and name `openkos repair` as the fix.

#### Scenario: Drift on disk does not reach the reader

- GIVEN exported `b` carries `status: stable`, and `a` holds a hand-written
  `supersedes` edge to `b` that `repair` has not yet projected
- WHEN the export runs
- THEN `out/b.md` carries `status: deprecated` and
  `status_derived_from: supersedes`, `bundle/b.md` is unchanged, and the
  report names `openkos repair`

#### Scenario: A withheld superseder still deprecates

- GIVEN exported `b` is superseded only by withheld `a`
- WHEN the export runs
- THEN `out/b.md` carries `status: deprecated`, and no file in `out/` names
  `a`

### Requirement: Body Links Into A Withheld Object Are Removed

Every markdown link in an exported document's body whose target resolves
inside the bundle to a `.md` path that is not an exported concept or a
bundle-root reserved file — in the bundle-relative (`/x.md`) or the
relative (`./x.md`, `../x.md`) form, inline or reference-style — MUST be
rewritten so that the output carries neither the withheld id nor a link to
it. Its label MUST be replaced by the fixed text `[withheld]`. Text outside
links MUST be kept as written. A link inside a fenced code block is text,
not a link, and is subject only to the leak check. Links to exported ids,
external URLs, and anchors MUST be kept unchanged. A reference-style
definition into a withheld object MUST be removed, and every use of its
reference label MUST become `[withheld]` (ADR-0048).

#### Scenario: A Related line into a confidential concept

- GIVEN exported `concepts/epicureanism` whose body holds
  `- [Stoicism](/concepts/stoicism.md) — contrasted with …`, and
  `concepts/stoicism` is withheld
- WHEN the export runs
- THEN that line in `out/concepts/epicureanism.md` reads
  `- [withheld] — contrasted with …`

#### Scenario: A link to an exported concept is kept

- GIVEN an exported body linking `/sources/notes-on-the-enchiridion-2026-07-05.md`,
  which is exported
- WHEN the export runs
- THEN the link is unchanged

### Requirement: Reserved Files Are Rebuilt For The Exported Set

The exported `index.md` MUST keep every line of `bundle/index.md` except a
bullet whose link resolves to an id that is not exported, and MUST drop a
section heading left with no entries; the bundle-root `okf_version`
frontmatter MUST be kept. The exported `log.md` MUST be a fresh log
following OKF §9 with one `**Export**` entry under the export date that
names no object, and MUST NOT carry any line of `bundle/log.md`. A bullet
of `index.md` is dropped when ANY of its links resolves to a withheld
object, not only its first.

#### Scenario: A withheld object leaves no index line

- GIVEN `bundle/index.md` lists `concepts/stoicism`, which is withheld
- WHEN the export runs
- THEN `out/index.md` has no line linking `concepts/stoicism`

#### Scenario: The workspace log does not leave

- GIVEN `bundle/log.md` records a `raw/` file name and a tombstone
- WHEN the export runs
- THEN `out/log.md` contains neither, and holds exactly one dated
  `**Export**` entry

### Requirement: The Output Is Self-Checked Before It Is Published

The export MUST build the whole output in a staging directory beside the
target and run two checks over the staged tree before publishing it: OKF
§11 conformance via `okf.check_conformance`, and a leak check that no
staged file's bytes contain any withheld concept id as a token: a
whitespace- or punctuation-delimited token that, after removing a leading
`/` and a trailing `.md` or `#anchor`, equals a withheld id, or that
resolves to one relative to the referring file. Here "withheld" means
every concept document in the bundle that is not exported. If either
check fails, the export MUST remove the staging directory, write nothing at
the target, name the failing file and rule, and exit 1. Only when both pass
MUST the staging directory be renamed to the target.

#### Scenario: A non-conformant document refuses the whole export

- GIVEN an exported concept whose transformed frontmatter would lack `type`
- WHEN the export runs
- THEN it exits 1, the target does not exist, and no staging directory
  remains

#### Scenario: A leak the filters missed refuses the whole export

- GIVEN an exported body that mentions a withheld id inside a fenced code
  block as `/concepts/stoicism.md`
- WHEN the export runs
- THEN it exits 1 naming that file, and nothing is published

### Requirement: The Export Never Writes The Workspace And Refuses A Torn Snapshot

`openkos export` MUST be read-only toward the workspace: it MUST NOT write,
commit, or take the workspace lock. After building the staged output and
before publishing it, it MUST re-read every input file it read and compare
its bytes; if any changed or vanished, it MUST remove the staging
directory, publish nothing, and exit 3, so that a re-run is safe.

#### Scenario: A concurrent write is refused

- GIVEN another process rewrites an exported concept while the export runs
- WHEN the export reaches its consistency check
- THEN it exits 3 and the target does not exist

#### Scenario: The workspace is untouched

- GIVEN any workspace
- WHEN `openkos export out/ --include-private --auto` succeeds
- THEN `git status` of the workspace is unchanged and no commit was made

### Requirement: `openkos export` Surface And Exit Codes

`openkos export <target>` MUST take one positional target directory and
the flags `--include-private`, `--allow-below-source` and `--auto`. The
target MUST NOT exist or MUST be an empty directory, its parent MUST exist,
and it MUST NOT be inside the workspace; otherwise the verb refuses with
exit 1 before reading the bundle. Before writing, the verb MUST print a preview stating how many
concepts will be exported and how many are withheld, grouped by reason
(`confidential`, `private` without `--include-private`, missing or
unrecognized label, unreadable, incomplete ingest, below its sources), the
ids the below-source rule withheld or admitted, and that prose outside
links is not redacted; it MUST then ask for confirmation unless `--auto` is
given or the workspace's `review` is `false`. A declined confirmation exits
1 with nothing written, and so does a non-interactive stdin without
`--auto` while review is on. When no concept is exportable, the verb MUST refuse
with exit 1, write nothing, and name `--include-private` if any concept was
withheld only for being `private`. Human-readable output MUST follow the
TTY-gated convention (ADR-0042). Exports of an unchanged workspace with the
same flags on the same day MUST be byte-identical.

#### Scenario: A stock workspace exports nothing by default

- GIVEN a workspace where every concept is `private`
- WHEN `openkos export out/` runs
- THEN it exits 1, `out/` is not created, and the message names
  `--include-private`

#### Scenario: A target inside the workspace is refused

- GIVEN a workspace at `kb/`
- WHEN `openkos export kb/bundle/out --auto` runs
- THEN it exits 1 before reading the bundle and nothing is written

#### Scenario: A non-empty target is refused

- GIVEN `out/` exists and contains a file
- WHEN `openkos export out/ --auto` runs
- THEN it exits 1 and `out/` is unchanged

#### Scenario: The canonical example exports its private half

- GIVEN `examples/good-life-demo`
- WHEN `openkos export out/ --include-private --auto` runs
- THEN `out/` holds exactly `concepts/epicureanism.md`,
  `sources/notes-on-the-enchiridion-2026-07-05.md`, `index.md` and
  `log.md`, `okf.check_conformance(out/)` is empty, and no file in `out/`
  links, relates to, or cites `concepts/stoicism`, `people/maria-salazar`,
  `decisions/frame-the-essay-on-the-dichotomy-of-control` or
  `sources/call-with-maria-2026-07-14`

#### Scenario: Exports are deterministic

- GIVEN an unchanged workspace
- WHEN `openkos export a/ --include-private --auto` and
  `openkos export b/ --include-private --auto` run on the same day
- THEN `a/` and `b/` are byte-identical
