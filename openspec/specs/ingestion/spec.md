# Ingestion Specification

## Purpose

`openkos ingest <path>` is the MVP 1 "null compiler": it copies a raw source
into the bundle, generates one conformant OKF Source concept with no LLM
extraction, records provenance OKF-natively, and updates the bundle catalog
(`index.md`) and log (`log.md`).

## Non-Goals

This spec does not define: LLM backend or extraction of concept content
beyond a single Source stub; JSON schema validation or bounded retry;
`--sensitivity` or `--batch` flags; model-quality evaluation; multi-concept
reconciliation (sensitivity high-water-mark, cross-concept links), deferred
to MVP 2 per `knowledge-object-model.md`.

## Requirements

### Requirement: Config Reader

The system MUST provide `read_config`, parsing `openkos.yaml` and returning
at least `model`, `review`, and `default_sensitivity`. It MUST NOT alter
`write_config`'s byte-identical template contract.

#### Scenario: Reads required fields

- GIVEN an initialized workspace with a valid `openkos.yaml`
- WHEN `read_config` runs
- THEN it returns `model`, `review`, and `default_sensitivity` matching the
  file's values

#### Scenario: No workspace config

- GIVEN a directory with no `openkos.yaml`
- WHEN `read_config` runs, directly or via `ingest`
- THEN it reports a clear error and performs no write

### Requirement: Bundle Catalog Append

The system MUST provide a primitive inserting a new entry into
`bundle/index.md` under the correct section, preserving all existing
entries and sections.

#### Scenario: New entry preserves existing catalog

- GIVEN an `index.md` with prior entries
- WHEN the primitive adds an entry for a new Source concept
- THEN the entry appears in the correct section and prior entries are
  unchanged

#### Scenario: Newline in title, slug, or description is rejected

- GIVEN a `title`, `slug`, or `description` value containing a newline
  (`\n` or `\r`)
- WHEN the catalog-append primitive is called with that value
- THEN it raises `ValueError` and `index.md` is left unchanged, preventing
  a source-derived value from forging a new Markdown section header

### Requirement: Bundle Log Append

The system MUST provide a primitive inserting a dated line into
`bundle/log.md`, preserving existing dated sections and entries.

#### Scenario: New dated line preserves existing log

- GIVEN a `log.md` with prior dated entries
- WHEN the primitive adds a line for the current local date
- THEN the line appears under the correct `## YYYY-MM-DD` section (created
  if absent) and prior entries are unchanged

#### Scenario: Newline in entry is rejected

- GIVEN an `entry` value containing a newline (`\n` or `\r`)
- WHEN the log-append primitive is called with that value
- THEN it raises `ValueError` and `log.md` is left unchanged, preventing a
  source-derived value from forging a new dated section header

### Requirement: Non-Exclusive Atomic Write

The system MUST provide an atomic write primitive (temp file + rename) for
updating files that already exist, separate from `write_exclusive`.
`write_exclusive` MUST remain create-only.

#### Scenario: Interrupted write leaves original intact

- GIVEN an existing `index.md` or `log.md` and a write interrupted before
  rename completes
- WHEN the bundle is inspected afterward
- THEN the original content is unchanged and no partial file replaces it

#### Scenario: write_exclusive stays create-only

- GIVEN a file that already exists
- WHEN `write_exclusive` targets that path
- THEN it refuses, unchanged from before this change

#### Scenario: write_exclusive cleans up its own partial file on write failure

- GIVEN `write_exclusive` has already created `path` in create-only ("x")
  mode and the subsequent write to that handle fails
- WHEN the failure occurs
- THEN `write_exclusive` unlinks the partially-written `path` before
  re-raising, so `path` does not exist afterward and a retry does not raise
  `FileExistsError` against its own leftover partial

### Requirement: Ingest Raw Copy and Source Concept Generation

`openkos ingest <path>` MUST copy the raw source into the bundle's raw
storage as an exclusive (create-only) binary write when `raw/<name>` does
not already exist for this source — this copy MUST remain byte-identical
and untouched afterward — and generate exactly one OKF Source concept with
frontmatter `type`, `title`, `description`, `resource`, `tags`,
`timestamp`, plus OpenKOS-layer `status`, `version`, `freshness`,
`sensitivity`, and `provenance`. WHEN `raw/<name>` already exists and the
incoming source's bytes are byte-identical to it, `ingest` MUST NOT
re-copy, rewrite, or delete the raw file, and MUST instead regenerate the
Source concept and the `index.md`/`log.md` catalog entries from the
existing raw bytes, exiting 0 — this holds regardless of whether
`bundle/sources/<slug>.md` already exists, and the catalog update MUST
NOT produce a duplicate entry when the concept was already catalogued.
WHEN `raw/<name>` does not exist but `bundle/sources/<slug>.md` does,
`ingest` MUST refuse, treating this as an inconsistent-workspace state.
WHEN the source decodes as UTF-8 text, the concept's BODY MUST embed that
text verbatim under a labeled section, followed by `# Citations`. WHEN the
source is not valid UTF-8 text, the body MUST instead contain a short,
honest note that the content could not be embedded as text (no crash),
followed by `# Citations`. An empty source MUST render a body distinct
from both the verbatim and undecodable cases. The generated concept MUST
pass `check_conformance`. The `description` MUST remain a single line (no
newlines) and MUST state that the raw source's content was embedded
verbatim and NOT extracted, compiled, or split into concepts — it MUST NOT
claim any extraction occurred.

#### Scenario: Successful ingest embeds verbatim text

- GIVEN an initialized workspace and a readable UTF-8 text source at
  `<path>`
- WHEN `openkos ingest <path>` completes (confirmed or `--auto`)
- THEN the raw source is copied, one Source concept exists whose body
  contains that source's text verbatim under a labeled section followed by
  `# Citations`, `description` is a single-line honest statement (embedded,
  not extracted), `check_conformance` reports no violations, and
  `index.md`/`log.md` reflect the new entry

#### Scenario: Path does not exist

- GIVEN `<path>` does not exist or is not readable
- WHEN `openkos ingest <path>` runs
- THEN it exits non-zero, writes a clear error to stderr, and no file is
  created or modified

#### Scenario: Raw absent but concept present is refused as an inconsistent workspace

- GIVEN `raw/<name>` does not exist but `bundle/sources/<slug>.md` already
  exists for this source
- WHEN `openkos ingest <path>` runs
- THEN it refuses in Phase A, exits non-zero with an error identifying the
  workspace as inconsistent (concept present, raw source missing), and
  writes nothing

#### Scenario: Byte-identical re-ingest regenerates the concept and catalog

- GIVEN `raw/<name>` already exists and the incoming source at `<path>` is
  byte-identical to it — whether or not `bundle/sources/<slug>.md` already
  exists (covering both the post-`forget` case, where the concept is
  absent, and the no-`forget` re-run case, where the concept is still
  present)
- WHEN `openkos ingest <path>` completes (confirmed or `--auto`)
- THEN `ingest` does not re-copy, rewrite, or delete `raw/<name>` (its
  bytes are unchanged), regenerates the Source concept and updates
  `index.md`/`log.md` with exactly one entry for this source (no
  duplicate), the preview shown beforehand names it a regenerate/update
  (not a new-raw copy), and the process exits 0

#### Scenario: Differing re-ingest under the same name is still refused

- GIVEN `raw/<name>` already exists and the incoming source at `<path>`
  differs in bytes from the existing `raw/<name>`
- WHEN `openkos ingest <path>` runs
- THEN it refuses in Phase A, exits non-zero with an error that
  distinguishes "content differs from the immutable raw copy" from the
  byte-identical case, and writes nothing (the raw file is not
  overwritten)

#### Scenario: Undecodable source falls back without crashing

- GIVEN a source at `<path>` that is not valid UTF-8 text (e.g. binary)
- WHEN `openkos ingest <path>` completes
- THEN `ingest` does not crash, the raw copy is still made byte-identical,
  and the Source concept's body honestly states the content could not be
  embedded as text, with no false claim of embedded content

#### Scenario: Empty source renders a distinct body

- GIVEN a source at `<path>` that is zero-length
- WHEN `openkos ingest <path>` completes
- THEN the Source concept's body distinctly indicates the source was
  empty, distinguishable from both the verbatim-embed and
  undecodable-fallback cases

### Requirement: Embedded Content Is Queryable End-to-End

Given a source has been ingested with its text embedded per the
requirement above, `openkos query "<question>"` MUST be able to retrieve
the resulting Source concept via the existing FTS index when the question
matches the embedded content, the LLM context assembled for the answer
MUST include that embedded content, and the rendered answer MUST cite that
Source concept. No change to `state/fts.py` or `retrieval/answer.py` is
required to satisfy this — embedding alone MUST make the content reachable
by the existing generic body-indexing and body-feeding behavior.

#### Scenario: Query retrieves and cites ingested content

- GIVEN a source ingested via `openkos ingest <path>` whose embedded body
  contains a distinctive phrase
- WHEN `openkos query "<question about that phrase>"` runs
- THEN the answer is not the no-match response, and the Source concept for
  `<path>` appears among the cited concepts

### Requirement: Path Containment

The raw copy destination and the concept slug MUST derive only from the
source's basename (path with directory components stripped) and a
sanitized slug. Directory-traversal or absolute-path segments in `<path>`
MUST NOT influence where the copy or concept document is written.

#### Scenario: Traversal segments are stripped, not followed

- GIVEN a source path containing traversal segments, e.g.
  `../../evil.txt`
- WHEN `openkos ingest ../../evil.txt` runs
- THEN the copied file lands inside the bundle's `raw/` directory (as
  `raw/evil.txt`), and no file is written outside `raw/` or
  `bundle/sources/`

#### Scenario: Empty slug after sanitization is refused

- GIVEN a source filename whose stem sanitizes to an empty slug (e.g. a
  stem made only of non-alphanumeric characters, such as `+++.txt`)
- WHEN `openkos ingest <path>` runs
- THEN it refuses in Phase A with a clear error, exits non-zero, and writes
  nothing (no raw copy, concept document, or catalog change)

### Requirement: OKF-Native Provenance

The system MUST record provenance as a `provenance:` frontmatter list of
raw source paths on the generated Source concept, with no separate
provenance store.

#### Scenario: Provenance recorded in frontmatter

- GIVEN a successful ingest of `<path>`
- WHEN the generated concept's frontmatter is inspected
- THEN `provenance` lists the raw path(s) for that source

### Requirement: Review/Confirm Flow

`ingest` MUST compute the Source concept, raw copy, and index/log changes
in memory during Phase A without writing, present a preview, and perform
Phase B writes only after confirmation. Each Phase B write MUST be
individually create-only (`copy_exclusive`, `write_exclusive`) or atomic
(`write_atomic`), and content MUST be written before the catalog (raw copy
and concept document before `index.md`/`log.md`), so the catalog never
references a file that does not exist. Phase B is NOT required to be
transactional as a whole: there is no rollback across the sequence, and a
failure partway through MAY leave a partial, detectable result recoverable
via git. `--auto` MUST skip the confirmation prompt and proceed directly
to Phase B.
Config `review: false` MUST likewise skip the prompt, the same as
`--auto`. When `review: true` and stdin is not a TTY and `--auto` is not
passed, the system MUST refuse to write rather than default silently —
this intentionally diverges from `init`'s silent-on-non-TTY behavior,
because `ingest` honors "review before save".

#### Scenario: Preview before write

- GIVEN a valid ingest target and interactive confirmation
- WHEN `openkos ingest <path>` runs without `--auto`
- THEN a preview of the raw copy, Source concept, and index/log changes is
  shown before any file is written

#### Scenario: Phase B writes proceed on confirm

- GIVEN a shown preview
- WHEN the user confirms
- THEN the raw copy, concept document, and index/log updates are written
  in order (content before catalog); on success all four land together

#### Scenario: Phase B failure leaves a detectable, recoverable partial result

- GIVEN a shown preview and confirmation
- WHEN a Phase B write past the first one fails
- THEN the command exits non-zero with a clear error and no raw traceback;
  writes already completed are NOT rolled back (no in-process undo); any
  resulting partial (e.g. an uncatalogued concept) is visible via
  `git status` and recoverable via `git checkout`/`git clean`

#### Scenario: --auto skips the prompt

- GIVEN a valid ingest target
- WHEN `openkos ingest <path> --auto` runs
- THEN no confirmation prompt is shown and Phase B writes proceed directly

#### Scenario: review: false skips the prompt like --auto

- GIVEN a workspace config with `review: false` and no `--auto` flag
- WHEN `openkos ingest <path>` runs
- THEN no confirmation prompt is shown and Phase B writes proceed directly

#### Scenario: Non-TTY without --auto refuses to write

- GIVEN `review: true` in config, stdin is not a TTY, and `--auto` is not
  passed
- WHEN `openkos ingest <path>` runs
- THEN it refuses to write, exits non-zero, tells the user to re-run with
  `--auto`, and nothing is written

### Requirement: Default Sensitivity from Config

The generated Source concept's `sensitivity` MUST equal the workspace
config's `default_sensitivity`; no `--sensitivity` flag is offered in this
slice.

#### Scenario: Sensitivity matches config default

- GIVEN a workspace config with `default_sensitivity: private`
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept's `sensitivity` field is `private`
