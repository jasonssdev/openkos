# Delta for Ingestion

## MODIFIED Requirements

### Requirement: Bounded, Deduplicated Derived-Object Staging

`ingest` MUST compute the complete set of derived objects to write with zero
writes (Phase A) before Phase B writes any of them. The number of derived
objects written for a single source MUST NOT exceed a backstop cap of 12,
applied exactly once, after union construction and judge selection (or after
the judge-failure degrade) — never as a pre-judge truncation. During
staging, the system MUST, per candidate in reply order: derive a slug from
the candidate's title and drop a candidate whose title yields an empty slug;
apply an in-batch slug-collision guard that keeps the first and drops later
candidate(s) from the SAME reply that slugify to an already-seen slug; and
drop a candidate whose fields fail the stricter single-line concept-build
gate. WHEN a candidate's slug collides with an existing on-disk concept whose
`provenance` already references THIS source, the candidate MUST be skipped
create-only (unchanged behavior — the existing file is left untouched). WHEN
a candidate's slug collides with an existing on-disk concept whose
`provenance` references a DIFFERENT source (or a slug the candidate's own
source previously won via disambiguation), the candidate MUST NOT be dropped;
instead it MUST be written to the first free numeric-suffixed slug
(`<slug>-2`, then `-3`, ...) with its own single-source `provenance`. A slug
MUST be reserved only once its candidate survives every check, so a dropped
or redirected candidate never reserves a slug for a later one. Each
per-candidate drop or disambiguation MUST be reported to stderr and MUST
affect only that candidate, never the whole batch.
(Previously: hard cap of 5 in spec text, 6 in code, applied as a blind
first-N truncation before any selection step; now a backstop of 12 applied
once, after union+judge selection.)

#### Scenario: More than the backstop of validated objects is bounded

- GIVEN a source whose union+judge selection would yield more than 12 valid
  objects
- WHEN `openkos ingest <path>` completes
- THEN no more than 12 derived objects are written

#### Scenario: Two objects in one reply collide on slug

- GIVEN a validated batch of two objects whose titles slugify to the same
  slug
- WHEN staging derived objects for write
- THEN only the first object in reply order is staged; the second is dropped
  with a note on stderr and not written

#### Scenario: Same-source slug collision is a create-only no-op

- GIVEN a validated candidate whose slug already exists on disk on a concept
  whose `provenance` references this ingest's source
- WHEN staging derived objects for write
- THEN that candidate is skipped (create-only), the existing file is left
  byte-untouched, and a note is emitted to stderr

#### Scenario: First foreign-source collision writes to `<slug>`

- GIVEN no existing concept file at the candidate's slug
- WHEN a first source's candidate is staged
- THEN it is written to `<slug>.md` with single-source `provenance`

#### Scenario: Second, different-source, same-title candidate writes to `<slug>-2`

- GIVEN an existing concept at `<slug>.md` whose `provenance` references a
  DIFFERENT source than the current candidate
- WHEN the current candidate is staged
- THEN it is written to `<slug>-2.md` with its own single-source
  `provenance`, and the existing `<slug>.md` is left untouched

#### Scenario: Third, different-source, same-title candidate writes to `<slug>-3`

- GIVEN `<slug>.md` and `<slug>-2.md` already exist, each owned by a
  different source than the current candidate
- WHEN the current candidate is staged
- THEN it is written to `<slug>-3.md`, the first free numeric suffix

## ADDED Requirements

### Requirement: Chunked-Source Candidates Feed Staging Unchanged

For a source that triggers chunking, staging MUST consume the judge-selected
(or judge-failure-degraded) candidate set produced from the existing
per-chunk extraction and merge, subject to the same backstop of 12 and the
same per-candidate slug/validation rules as an unchunked source. Chunking
itself introduces no separate staging path.

#### Scenario: Chunked source staging uses the same cap and rules

- GIVEN a source long enough to be split into chunks during extraction
- WHEN `openkos ingest <path>` completes
- THEN staged derived objects obey the same 12-object backstop and slug
  rules as an unchunked source, with no chunk-specific exception

### Requirement: Judge-Failure Degrade Is Reported, Ingest Still Succeeds

WHEN the extraction judge call fails (raises `OllamaError`, returns an
empty or unparseable reply), `ingest` MUST proceed with the merged-union
candidates truncated by the backstop cap rather than falling back to
Source-only, MUST emit a note to stderr distinct from the Source-only
degrade notice, and MUST exit 0.

#### Scenario: Judge failure keeps validated candidates, not just the Source

- GIVEN a fake LLM backend whose base extraction succeeds but whose judge
  call raises `OllamaError`
- WHEN `openkos ingest <path>` runs
- THEN the merged-union candidates (backstop-truncated) are written as
  derived objects, a judge-failure note appears on stderr, and the exit
  code is 0

#### Scenario: Full LLM unavailability still falls back to Source-only

- GIVEN a fake LLM backend whose `chat` call raises a backend error during
  the base extraction call itself (not the judge)
- WHEN `openkos ingest <path>` runs
- THEN behavior is unchanged from the existing "Extraction Degrades
  Gracefully on LLM Unavailability" requirement — only the Source concept
  is written

### Requirement: Pre-Archive Measurement Gate

Before this change is archived, before/after runs on
`evals/extraction_cap/run_cap_eval.py` and the AMI type-coverage harness
MUST show recall not regressed on any fixture: genuine-subject retention
MUST NOT decrease and known-facet retention MUST NOT increase, on every
measured fixture including chunked transcripts.

#### Scenario: Eval gate blocks archive on regression

- GIVEN before/after eval runs comparing the single-cap path to the
  union+judge path
- WHEN any fixture shows decreased genuine-subject recall or increased
  facet retention
- THEN the change MUST NOT be archived until the regression is resolved
