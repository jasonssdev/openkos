# Spec: add-llm-concept-extraction (Delta - Ingestion Domain)

## Domain: ingestion (MODIFIED & ADDED)

### MODIFIED Requirements

#### Requirement: Ingest Raw Copy and Source Concept Generation

`openkos ingest <path>` MUST copy the raw source into the bundle's raw storage as an exclusive (create-only) binary write and generate exactly one OKF Source concept, as before. In addition, `ingest` MUST attempt LLM-driven extraction of **at most one** derived object of type `Concept` or `Entity` from the source. WHEN extraction succeeds and the derived object passes validation, `ingest` MUST write that derived object IN ADDITION to the Source concept, with `provenance` pointing to the Source and `sensitivity` inherited from the Source. WHEN extraction fails, is unavailable, times out, errors, or produces invalid output, `ingest` MUST degrade to Source-only behavior — write only the Source concept, emit an explanatory note to stderr, and exit 0 (no crash). Extraction always runs regardless of `--auto`; `--auto` only skips the confirmation prompt.

(Previously: `ingest` never attempted LLM extraction and always emitted exactly one Source concept per invocation.)

- Scenario: Successful ingest embeds verbatim text — GIVEN an initialized workspace and a readable UTF-8 text source at `<path>` WHEN `openkos ingest <path>` completes (confirmed or `--auto`) THEN the raw source is copied, one Source concept exists whose body contains that source's text verbatim, `check_conformance` reports no violations, and `index.md`/`log.md` reflect the new entry
- Scenario: Path does not exist — GIVEN `<path>` does not exist or is not readable WHEN `openkos ingest <path>` runs THEN it exits non-zero, writes a clear error to stderr, and no file is created or modified
- Scenario: Already-ingested source is refused, not overwritten — GIVEN `raw/<name>` or `bundle/sources/<slug>.md` already exists for this source WHEN `openkos ingest <path>` runs THEN it refuses in Phase A, exits non-zero with a clear error, and writes nothing
- Scenario: Successful extraction yields a Concept — GIVEN a source whose content clearly describes an idea, topic, or framework, and a fake LLM backend returning a well-formed structured reply of `type: Concept` WHEN `openkos ingest <path>` completes THEN both the Source concept AND a Concept document are written, the Concept's `provenance` references the Source, and `check_conformance` reports no violations for either document
- Scenario: Successful extraction yields an Entity — GIVEN a source whose content clearly describes a concrete tool, product, or artifact that is not a person or organization, and a fake LLM backend returning a well-formed structured reply of `type: Entity` WHEN `openkos ingest <path>` completes THEN both the Source concept AND an Entity document are written, and the Entity's `provenance` references the Source

### ADDED Requirements

#### Requirement: Type Classification Prefers Specific Types Over the Entity Fallback

Extraction MUST classify the derived object's type using a closed vocabulary of `{Concept, Entity}`. `Entity` MUST be used only as a fallback when no more specific type fits; `Concept` MUST be preferred whenever the source content describes an idea, topic, theory, term, or framework.

- Scenario: Entity chosen only when Concept does not fit — GIVEN a fake LLM backend that would only plausibly classify the source's content as a concrete artifact rather than an idea or framework WHEN extraction runs THEN the derived object's `type` is `Entity`, not `Concept`
- Scenario: Concept preferred when content fits — GIVEN a fake LLM backend returning a reply describing an idea or framework WHEN extraction runs THEN the derived object's `type` is `Concept`

#### Requirement: Fail-Closed Validation of Extracted Output

Extraction output MUST be validated before any derived object is written. Validation MUST reject: output that is not parseable as the documented structured shape; a `type` outside `{Concept, Entity}`; and missing or empty required fields (at minimum `title` and `description`). WHEN validation rejects the output, `ingest` MUST NOT write a derived object, MUST still write the Source concept, MUST emit a note to stderr explaining the degrade, and MUST exit 0.

- Scenario: Malformed JSON degrades to Source-only — GIVEN a fake LLM backend returning a reply that is not valid structured output WHEN `openkos ingest <path>` runs THEN only the Source concept is written, a note appears on stderr, and the exit code is 0
- Scenario: Invalid type degrades to Source-only — GIVEN a fake LLM backend returning well-formed output whose `type` is outside `{Concept, Entity}` WHEN `openkos ingest <path>` runs THEN only the Source concept is written, a note appears on stderr, and the exit code is 0
- Scenario: Missing title degrades to Source-only — GIVEN a fake LLM backend returning output with an empty or missing `title` WHEN `openkos ingest <path>` runs THEN only the Source concept is written, a note appears on stderr, and the exit code is 0

#### Requirement: Extraction Degrades Gracefully on LLM Unavailability

WHEN the LLM backend raises an error (unavailable, timeout, or any backend error) during extraction, `ingest` MUST catch it locally, degrade to Source-only behavior, emit a note to stderr, and exit 0. Extraction failure MUST NOT crash or abort the ingest command.

- Scenario: LLM backend unavailable — GIVEN a fake LLM backend whose `chat` call raises a backend error WHEN `openkos ingest <path>` runs THEN only the Source concept is written, a note describing the degrade appears on stderr, and the command exits 0

#### Requirement: Derived Object Provenance and Sensitivity Inheritance

A successfully validated derived object MUST record `provenance` referencing its originating Source concept, and MUST inherit the Source's `sensitivity` value verbatim.

- Scenario: Provenance and sensitivity inherited — GIVEN a source ingested with a configured `sensitivity` value and successful extraction WHEN `openkos ingest <path>` completes THEN the derived object's frontmatter `provenance` includes a reference to the Source concept and its `sensitivity` equals the Source's `sensitivity`

#### Requirement: Review Gate Shows Both Objects Before Write

The confirmation preview MUST show BOTH the proposed Source concept and the proposed derived object (when extraction succeeded) before any write occurs. WHEN `--auto` is passed, both objects MUST be written without prompting; the confirmation prompt is skipped, but extraction still runs beforehand.

- Scenario: Interactive confirm shows both objects — GIVEN successful extraction and an interactive TTY without `--auto` WHEN `openkos ingest <path>` reaches the confirm gate THEN the preview lists both the Source concept and the derived object, and declining aborts with no files written
- Scenario: `--auto` writes both without prompting — GIVEN successful extraction and `--auto` WHEN `openkos ingest <path>` runs THEN no confirmation prompt appears and both the Source concept and the derived object are written

#### Requirement: Idempotent Re-Ingest Leaves an Existing Derived Object Untouched

WHEN a source is re-ingested and a derived object already exists (from a prior successful extraction) for that source, `ingest` MUST leave the existing derived object file untouched — no overwrite, no re-extraction of that object's content.

- Scenario: Re-ingest does not overwrite existing derived object — GIVEN a source already ingested with a resulting Concept (or Entity) document, possibly hand-edited afterward, WHEN `openkos ingest <path>` is run again for the same source THEN the existing derived object file's content is unchanged

#### Requirement: Derived Object Cataloging and Logging

A successfully written derived object MUST be cataloged in `index.md` under the `# Concepts` section, and the write MUST be recorded as a new entry in `log.md`, alongside the Source concept's own catalog and log entries.

- Scenario: Catalog and log reflect both objects — GIVEN successful extraction and a completed ingest WHEN `index.md` and `log.md` are inspected THEN `index.md` lists the Source under `# Sources` and the derived object under `# Concepts`, and `log.md` records both writes

## Testability Note

Extraction scenarios MUST be exercised via a fake, structural `LLMBackend` (mirroring `_FakeLLM` in `tests/unit/retrieval/test_answer.py:41-50`) that records `chat()` calls and returns a fixed, canned reply — never a real model call. This keeps extraction, classification, and validation scenarios fully deterministic: each scenario configures the fake's reply to represent the case under test (well-formed Concept, well-formed Entity, malformed JSON, invalid type, missing title, or a raised backend error).

## Non-Goals (recorded)

Multiple derived objects per ingest; the other 9 canonical OKF types; entity resolution/merge/reclassification on re-ingest; typed relationship graph; sensitivity high-water-mark across multiple sources; MVP-2 hybrid retrieval.
