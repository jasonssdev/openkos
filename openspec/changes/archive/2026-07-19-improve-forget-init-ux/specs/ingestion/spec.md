# Delta for Ingestion

## MODIFIED Requirements

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
WHEN the source decodes as UTF-8 text, the concept's
BODY MUST embed that text verbatim under a labeled section, followed by
`# Citations`. WHEN the source is not valid UTF-8 text, the body MUST
instead contain a short, honest note that the content could not be
embedded as text (no crash), followed by `# Citations`. An empty source
MUST render a body distinct from both the verbatim and undecodable cases.
The generated concept MUST pass `check_conformance`. The `description`
MUST remain a single line (no newlines) and MUST state that the raw
source's content was embedded verbatim and NOT extracted, compiled, or
split into concepts — it MUST NOT claim any extraction occurred.
(Previously: any pre-existing `raw/<name>` unconditionally refused
re-ingest in Phase A, regardless of whether the incoming bytes matched.)

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
</content>
