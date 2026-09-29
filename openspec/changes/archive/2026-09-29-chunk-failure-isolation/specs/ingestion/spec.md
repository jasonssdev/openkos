# Delta for ingestion

## MODIFIED Requirements

### Requirement: Extraction Degrades Gracefully on LLM Unavailability

WHEN the LLM backend raises an error (unavailable, timeout, or any backend
error) during extraction and NO chunk-level isolation elsewhere in this
spec applies -- i.e. the source is below the chunking threshold (a single
extraction call), the backend is unreachable (`BackendUnavailable`), or
every chunk's retry has also failed -- `ingest` MUST catch it locally,
degrade to Source-only behavior, emit a note to stderr, and exit 0.
Extraction failure MUST NOT crash or abort the ingest command.

#### Scenario: LLM backend unavailable

- GIVEN a fake LLM backend whose `chat` call raises a backend error
- WHEN `openkos ingest <path>` runs
- THEN only the Source concept is written, a note describing the degrade
  appears on stderr, and the command exits 0

#### Scenario: A source below the chunking threshold degrades on any backend error

- GIVEN a source whose length does not trigger chunking, and a fake LLM
  backend whose single extraction call raises `OllamaError`
- WHEN `openkos ingest <path>` runs
- THEN only the Source concept is written (no retry is attempted; this
  path has no chunk to isolate), a note describing the degrade appears on
  stderr, and the command exits 0

#### Scenario: An unreachable backend degrades on the first chunk failure, not the last

- GIVEN a chunked source and a fake LLM backend whose `chat` call raises
  `OllamaUnavailable` on one window
- WHEN `openkos ingest <path>` runs
- THEN only the Source concept is written, no retry is attempted for that
  window, and no later window is ever attempted

#### Scenario: Every chunk failing even after its retry still degrades

- GIVEN a chunked source and a fake LLM backend whose `chat` call raises
  `OllamaGenerationCapped` on EVERY chunk, including each chunk's one
  retry
- WHEN `openkos ingest <path>` runs
- THEN only the Source concept is written, the same stderr degrade note
  appears, and the command exits 0 -- byte-identical to a single-window
  failure before chunk isolation existed

## ADDED Requirements

### Requirement: Chunked Extraction Isolates a Single Failed Window

WHEN a chunked source's extraction call for ONE window raises a
`BackendError`-family exception (the backend-agnostic base class
`OllamaError` and its siblings subclass) OTHER than `BackendUnavailable`,
the system MUST retry that window's call EXACTLY ONCE. WHEN the retry also
raises a `BackendError`-family exception, that window MUST be skipped --
contributing no objects -- while every OTHER window's extraction results
MUST still be merged, filtered, judged (on the default `union_judge` path),
and staged normally, exactly as if the skipped window had answered `[]`.
The 1-indexed position of every skipped window MUST be recorded on the
extraction report. WHEN every window in the fan-out is skipped this way
(every window's retry also failed), the system MUST instead degrade the
WHOLE source to Source-only, per the "Extraction Degrades Gracefully on
LLM Unavailability" requirement -- a skip is never silently indistinguishable
from a total failure.

`BackendUnavailable` (the backend could not be reached at all) MUST NEVER
be retried or skipped: it MUST propagate on its FIRST occurrence, exactly
as before this requirement existed, because an unreachable backend will
not answer a different window either.

WHEN at least one window is skipped and at least one window survives, the
system MUST print a stderr line naming every skipped window by its
1-indexed position and the total window count (e.g. "chunk 3 of 12"), and
MUST write the `chunk-extraction-partial` `extraction_notice` frontmatter
token onto the Source (per the `extraction_notice` closed vocabulary),
alongside any other `extraction_notice` token the same run also carries.

A byte-identical re-ingest of a Source carrying `chunk-extraction-partial`
MUST re-run extraction (the same "retryable debt" treatment as the two
judge-degrade `extraction_notice` tokens), even without `--re-extract`.

#### Scenario: A single failed chunk is retried and recovers

- GIVEN a chunked source and a fake LLM backend whose `chat` call raises
  `OllamaGenerationCapped` on window 2's FIRST attempt only, succeeding on
  its retry and on every other window
- WHEN `openkos ingest <path>` runs
- THEN every window's object is written, no `extraction_notice` is set,
  and no degrade note appears on stderr

#### Scenario: A chunk that fails its retry is skipped, others are kept

- GIVEN a chunked source of 12 windows and a fake LLM backend whose `chat`
  call raises `OllamaGenerationCapped` on window 3's first attempt AND its
  retry, succeeding on every other window
- WHEN `openkos ingest <path>` runs
- THEN 11 windows' objects are written, the command exits 0, stderr names
  "chunk 3 of 12", and the Source's `extraction_notice` includes
  `chunk-extraction-partial`

#### Scenario: Every chunk fails even after retry degrades the whole source

- GIVEN a chunked source and a fake LLM backend whose `chat` call raises
  `OllamaGenerationCapped` on every window, including every retry
- WHEN `openkos ingest <path>` runs
- THEN only the Source concept is written, the existing "concept
  extraction skipped" degrade note appears on stderr, `extraction_status`
  is `failed`, and no `extraction_notice` is set

#### Scenario: A byte-identical re-ingest retries a chunk-partial Source

- GIVEN a Source whose frontmatter carries
  `extraction_notice: chunk-extraction-partial` from a prior run
- WHEN `openkos ingest raw/<name>` is re-run against the same bytes,
  without `--re-extract`
- THEN extraction runs again (the byte-identical convergence skip does
  NOT apply), exactly as it already does for the two judge-degrade tokens
