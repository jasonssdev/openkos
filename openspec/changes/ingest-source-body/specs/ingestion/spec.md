# Delta for Ingestion

## MODIFIED Requirements

### Requirement: Ingest Raw Copy and Source Concept Generation

`openkos ingest <path>` MUST copy the raw source into the bundle's raw
storage as an exclusive (create-only) binary write — this copy MUST remain
byte-identical and untouched afterward — and generate exactly one OKF
Source concept with frontmatter `type`, `title`, `description`, `resource`,
`tags`, `timestamp`, plus OpenKOS-layer `status`, `version`, `freshness`,
`sensitivity`, and `provenance`. WHEN the source decodes as UTF-8 text, the
concept's BODY MUST embed that text verbatim under a labeled section,
followed by `# Citations`. WHEN the source is not valid UTF-8 text, the
body MUST instead contain a short, honest note that the content could not
be embedded as text (no crash), followed by `# Citations`. An empty source
MUST render a body distinct from both the verbatim and undecodable cases.
The generated concept MUST pass `check_conformance`. The `description`
MUST remain a single line (no newlines) and MUST state that the raw
source's content was embedded verbatim and NOT extracted, compiled, or
split into concepts — it MUST NOT claim any extraction occurred.

(Previously: body was boilerplate-only `# Citations` with no source text;
`description` stated only that the source "has not yet been compiled or
extracted", without addressing content presence.)

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

#### Scenario: Already-ingested source is refused, not overwritten

- GIVEN `raw/<name>` or `bundle/sources/<slug>.md` already exists for this
  source
- WHEN `openkos ingest <path>` runs
- THEN it refuses in Phase A, exits non-zero with a clear error, and writes
  nothing

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

## ADDED Requirements

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
