# Source Title Backfill Specification

## Purpose

`openkos backfill-source-titles` is a dedicated, bundle-wide sweep that
closes the title gap left by Sources ingested before
`source-title-from-heading` (#248): it re-derives each `type: source`
concept's `title` from its immutable `raw/` bytes via
`derive_source_title`, in one three-bucket preview, one confirmation, one
`log.md` entry, and one commit — mirroring `backfill-sensitivity`'s shape
over a pure core.

## Non-Goals

This spec does not define: a `<concept-id>` scoping argument (bundle-wide
only — re-running `openkos ingest` on a byte-identical file already
regenerates a single Source's title); any rewrite of historical `log.md`
entries (the old title stays in history by design — decision 1 of the
proposal); the companion lint check "Source title still equals its slug"
(separate follow-up, #248's own *What Is Still Owed* list); any slug,
filename, or Concept ID rename; any rebuild of
`.openkos/{fts,vectors,graph}.db` (`reindex` stays the sole, always-manual
writer); full-document regeneration via `build_source_concept` (the
original `<path>` needed to reconstruct `description` is not available to
a backfill); and any change to `openkos ingest` behavior.

## Requirements

### Requirement: Bundle-Wide Sweep With No Concept Argument

`openkos backfill-source-titles` MUST take no positional concept-id
argument and MUST consider every concept of `type: source` in the bundle
as a candidate; no other concept type MUST be evaluated or written by this
command.

#### Scenario: Only type-source concepts are considered

- GIVEN a bundle containing Source concepts and non-Source derived concepts
  (e.g. `Concept`, `Entity`)
- WHEN `openkos backfill-source-titles` runs
- THEN only the Source concepts are evaluated for staging, and no
  non-Source concept is read, staged, or written

#### Scenario: The command accepts no concept-id argument

- GIVEN a bundle with any number of Sources
- WHEN `openkos backfill-source-titles` is invoked
- THEN it accepts no positional concept identifier and sweeps every Source
  in the bundle in a single run

### Requirement: Three-Bucket Classification In Fixed Evaluation Order

For each Source, the command MUST classify it into exactly one of three
buckets, evaluated in this exact order, and MUST stop at the first bucket
that applies:

1. **Warned (malformed resource)**: the Source's `resource` field is
   absent, does not start with `raw/`, contains a `..` path segment, or
   does not resolve to a path under `raw/`. The Source MUST be reported in
   the warned bucket and MUST NOT be staged.
2. **Curated (skipped)**: `title != _titleize(Path(resource).stem)`. The
   Source MUST be reported in the curated bucket and MUST NOT be staged.
3. **Mechanical (re-derive)**: otherwise, the command MUST re-derive a
   candidate title from the `raw/<name>` bytes via `derive_source_title`.
   The Source MUST be staged only when the derived result is non-`None`
   AND differs from the current `title`. A `None` result, or a result
   identical to the current `title`, MUST stage nothing for that Source.

#### Scenario: Malformed resource is warned and never staged

- GIVEN a Source concept whose `resource` field is absent, does not begin
  with `raw/`, contains a `..` segment, or does not resolve under `raw/`
- WHEN `openkos backfill-source-titles` runs
- THEN that Source appears in the warned bucket of the preview, is never
  staged, and its frontmatter and body are byte-identical after the run

#### Scenario: A curated title is skipped, not staged

- GIVEN a Source whose `resource` is well-formed and whose `title` does
  NOT equal `_titleize(Path(resource).stem)`
- WHEN `openkos backfill-source-titles` runs
- THEN that Source appears in the curated bucket of the preview and is
  never staged, regardless of what `derive_source_title` would return for
  its raw bytes

#### Scenario: A mechanical title with a differing derivation is staged

- GIVEN a Source whose `resource` is well-formed, whose `title` equals
  `_titleize(Path(resource).stem)`, and whose `raw/<name>` bytes, passed
  through `derive_source_title`, yield a non-`None` title different from
  the current one
- WHEN `openkos backfill-source-titles` runs
- THEN that Source is staged with the newly derived title

#### Scenario: A `None` re-derivation stages nothing

- GIVEN a Source that passes the mechanical test but whose `raw/<name>`
  bytes yield `None` from `derive_source_title`
- WHEN `openkos backfill-source-titles` runs
- THEN that Source is not staged and is not written

#### Scenario: An identical re-derivation stages nothing

- GIVEN a Source that passes the mechanical test and whose re-derived
  title from `raw/<name>` equals the current `title` exactly
- WHEN `openkos backfill-source-titles` runs
- THEN that Source is not staged and is not written

#### Scenario: The `01-Introduction.md` counterexample classifies as mechanical, not curated

- GIVEN a Source ingested from `01-Introduction.md`, storing `title: 01
  Introduction` (i.e. `_titleize("01-Introduction")`), where
  `_titleize(slug)` (`_titleize("01-introduction")`) would instead yield
  `01 introduction`
- WHEN `openkos backfill-source-titles` evaluates this Source
- THEN it is classified as mechanical (bucket 3), NOT curated, because the
  test compares `title` against `_titleize(Path(resource).stem)` — not
  against `_titleize(slug)` — and `Path(resource).stem` equals
  `"01-Introduction"`, matching the stored title exactly

### Requirement: Body First-Line Safety Property

Before overwriting a staged Source's document, the command MUST read the
document's literal first body line and MUST verify it equals exactly
`# {current_title}`, where `current_title` is the Source's on-disk `title`
value before this run. WHEN the first body line does not match this exact
form, the command MUST refuse to write that Source, MUST report it as
refused, and MUST leave that Source's frontmatter and body byte-identical
to before the run — even when that same Source passed the mechanical
classification test in the preceding requirement.

#### Scenario: A hand-edited first line is refused, not overwritten

- GIVEN a Source staged for a title change, whose document's first body
  line does NOT read exactly `# {current_title}` (e.g. it was hand-edited
  after ingest)
- WHEN `openkos backfill-source-titles` runs and reaches Phase B for this
  Source
- THEN the write for this Source is refused, it is reported as refused in
  the run's output, and its frontmatter and body remain byte-identical to
  before the run

#### Scenario: A matching first line is overwritten normally

- GIVEN a Source staged for a title change, whose document's first body
  line reads exactly `# {current_title}`
- WHEN `openkos backfill-source-titles` runs and is confirmed
- THEN the first body line is rewritten to `# {new_title}` alongside the
  frontmatter `title:` update

### Requirement: Exactly Two Byte-Level Edits Per Staged Source

For each staged Source, the command MUST change exactly two things in the
document: the frontmatter `title:` value, and the document body's literal
first line (from `# {current_title}` to `# {new_title}`). The
`description` field, the `## Source content` section, the `# Citations`
section, every other frontmatter key, and every other line of the body
MUST remain byte-identical to before the run.

#### Scenario: Only title and first line change

- GIVEN a Source staged for a title change with a well-formed body
- WHEN the write completes
- THEN a byte-level diff of the document shows changes only to the
  frontmatter `title:` value and the first body line; `description`,
  `## Source content`, `# Citations`, and all other frontmatter keys are
  unchanged

### Requirement: `index.md` Bullet Label Update

For each staged Source, the command MUST update that Source's bullet entry
in `index.md` so its label reflects the new title. The bullet's slug, link
target, and `description` text MUST remain unchanged.

#### Scenario: The index bullet label reflects the new title

- GIVEN a Source staged for a title change, listed in `index.md` under
  `# Sources`
- WHEN `openkos backfill-source-titles` runs and is confirmed
- THEN that Source's bullet in `index.md` shows the new title as its
  label, while its slug, link target, and `description` text are
  unchanged

#### Scenario: Unstaged Sources' index bullets are untouched

- GIVEN a bundle containing both staged and unstaged (warned or curated)
  Sources, all listed in `index.md`
- WHEN `openkos backfill-source-titles` runs and is confirmed
- THEN only the staged Sources' bullets change label text; every other
  bullet in `index.md` is byte-identical to before the run

### Requirement: Invariants Preserved Across Every Run

The following MUST hold after any invocation of
`openkos backfill-source-titles`, regardless of outcome: `raw/` bytes are
never written or modified by this command; historical `log.md` entries
already on disk before the run remain byte-identical (the old title
persists in history by design); the slug, filename, and Concept ID of
every Source are unchanged; and `.openkos/{fts,vectors,graph}.db` are
never written or modified by this command.

#### Scenario: `raw/` bytes are untouched

- GIVEN a bundle with Sources staged for a title change
- WHEN `openkos backfill-source-titles` runs and is confirmed
- THEN every file under `raw/` is byte-identical before and after the run

#### Scenario: Historical `log.md` entries keep the old title

- GIVEN a Source's original ingest `log.md` entry recorded the old title
- WHEN that Source is staged and its title is backfilled
- THEN the pre-existing `log.md` entry from ingest still shows the old
  title, unchanged; only a new entry (per the logging requirement below)
  is added

#### Scenario: Slug, filename, and Concept ID never change

- GIVEN a Source staged for a title change
- WHEN the write completes
- THEN the Source's filename, slug, and Concept ID are identical to
  before the run

#### Scenario: The derived-index databases are untouched

- GIVEN a bundle with `.openkos/fts.db`, `.openkos/vectors.db`, and
  `.openkos/graph.db` present
- WHEN `openkos backfill-source-titles` runs, staging one or more title
  changes and confirmed
- THEN all three database files are byte-identical before and after the
  run

### Requirement: Empty-Result Short Circuit

WHEN a run stages zero Sources — because no Source is mechanical with a
differing re-derivation, or the bundle contains no Sources at all — the
command MUST report that nothing was staged, MUST write no file, MUST
create no commit, and MUST exit 0.

#### Scenario: A fully curated or warned bundle is a no-op

- GIVEN a bundle where every Source is either curated or warned (malformed
  resource)
- WHEN `openkos backfill-source-titles` runs
- THEN it prints a message reporting that nothing was staged, writes no
  file, creates no commit, and exits 0

#### Scenario: A bundle with no Sources is a no-op

- GIVEN a bundle containing zero `type: source` concepts
- WHEN `openkos backfill-source-titles` runs
- THEN it prints a message reporting that nothing was staged, writes no
  file, creates no commit, and exits 0

### Requirement: Three-Bucket Preview Then Confirm Gate

Before any write, the command MUST print one preview listing all three
buckets — staged, curated (skipped), and warned (malformed resource) — for
every Source evaluated. After the preview, the command MUST apply the same
confirm-gate precedence `backfill-sensitivity` uses: `--auto` skips the
prompt, workspace config `review: false` skips the prompt, an interactive
TTY prompts via `typer.confirm`, and a non-interactive session without
`--auto` refuses with a non-zero exit and no write.

#### Scenario: Preview shows all three buckets before any prompt

- GIVEN a run with at least one Source in each of the staged, curated, and
  warned buckets
- WHEN `openkos backfill-source-titles` runs on a TTY without `--auto`
- THEN the preview lists all three buckets before the confirm prompt is
  shown

#### Scenario: `--auto` skips the prompt only

- GIVEN staged title changes exist
- WHEN `openkos backfill-source-titles --auto` runs
- THEN the changes are written without any confirmation prompt

#### Scenario: `review: false` skips the prompt like `--auto`

- GIVEN a workspace config with `review: false` and staged title changes
- WHEN `openkos backfill-source-titles` runs without `--auto`
- THEN no confirmation prompt is shown and the write proceeds directly

#### Scenario: Non-TTY without `--auto` refuses to write

- GIVEN no TTY is attached, `--auto` is absent, and staged title changes
  are pending
- WHEN `openkos backfill-source-titles` runs
- THEN the command refuses with a non-zero exit and no file is written

#### Scenario: Declining the prompt performs no write

- GIVEN an interactive TTY and staged title changes pending
- WHEN `openkos backfill-source-titles` runs and the confirm prompt is
  declined
- THEN no concept file, `index.md`, or `log.md` is written, and no commit
  is created

### Requirement: Idempotence

Re-running `openkos backfill-source-titles` on a bundle where a prior run
already converged every mechanical Source MUST stage nothing.

#### Scenario: Immediate re-run after a successful sweep is a no-op

- GIVEN a bundle where `openkos backfill-source-titles` just completed
  successfully, backfilling all mechanical Sources
- WHEN `openkos backfill-source-titles` runs again immediately
- THEN it reports that nothing was staged, writes nothing, creates no
  commit, and exits 0

### Requirement: Atomicity And Partial-Failure Reporting

Each staged Source's write (its document plus its `index.md` bullet
update) MUST be applied atomically per Source. The command MUST maintain a
`landed` accumulator of Sources successfully written so far. WHEN a write
fails partway through the sweep, the command MUST NOT roll back any
already-landed Source, MUST leave the bundle in its landed-so-far state,
and MUST report a failure message that names every Source that had
already landed before the failure.

#### Scenario: A mid-sweep write failure names the paths that already landed

- GIVEN a sweep staging title changes across multiple Sources, where the
  write fails after the first two Sources are written but before the
  third
- WHEN `openkos backfill-source-titles` runs and the failure occurs
- THEN the command exits non-zero, the first two Sources remain updated on
  disk, and the failure message names both of their paths explicitly

### Requirement: One Log Entry And One Autocommit For The Whole Sweep

A successful run MUST write every staged Source's title change, then
append exactly one dated entry to `bundle/log.md` summarizing the whole
sweep, then create exactly one `_autocommit` covering every changed path
(each staged Source document, `index.md`, and the new `log.md` entry).

#### Scenario: A multi-Source run produces one log entry and one commit

- GIVEN staged title changes across three different Sources
- WHEN `openkos backfill-source-titles` runs and is confirmed
- THEN exactly one new `log.md` entry is appended and exactly one commit
  covers every changed Source document, `index.md`, and that `log.md`
  entry
