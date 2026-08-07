# Extraction Union-Judge Specification

## Purpose

Replace the blind, position-based `_MAX_OBJECTS_PER_SOURCE` truncation with a
union-of-runs + selector-judge pipeline: run extraction twice (or once per
chunk), merge candidates deterministically, let a judge select the genuine
subset from a closed candidate list, and apply a numeric backstop cap only
after judge selection — never before it.

## Requirements

### Requirement: Union Construction Below the Chunk Threshold

For a source at or below `_CHUNK_THRESHOLD`, the system MUST run `_extract_once`
twice with the same prompt/messages and MUST merge the two runs' validated
candidates into a single union before judging.

#### Scenario: Two runs contribute distinct genuine subjects

- GIVEN two `_extract_once` calls returning different valid candidate sets
  for the same source
- WHEN the orchestrator builds the union
- THEN candidates unique to either run are both present in the merged
  candidate list passed to the judge

### Requirement: Chunked Sources Are Judge-Only, No Second Pass

For a source above `_CHUNK_THRESHOLD`, the system MUST NOT run a
second extraction pass per chunk. It MUST judge over the existing per-chunk
merged candidate set exactly as produced today, and this is the permanent
shape for chunked sources, not an interim state.

#### Scenario: Chunked source skips the second run

- GIVEN a source whose length triggers chunking
- WHEN extraction runs
- THEN each chunk is extracted exactly once, the chunk merge proceeds as
  before, and the judge evaluates that single merged set

### Requirement: Per-Run Twin-Drop and Richer-Body Merge

Each run's own output MUST have source-title twins dropped (respecting the
existing `Procedure` exemption) before it enters the union. WHEN merging the
union, a `(type, normalized-title)` collision between candidates from
different runs MUST be resolved by keeping the candidate with the richer
body/description, not by first-occurrence order.

#### Scenario: Twin-drop applies per run before merge

- GIVEN one run whose output contains a title-twin of the source
- WHEN that run's candidates are prepared for the union
- THEN the twin is dropped from that run's contribution before merging,
  independent of the other run's output

#### Scenario: Richer body wins on collision

- GIVEN two candidates from different runs with the same `(type,
  normalized-title)` but different body lengths
- WHEN the union is merged
- THEN the candidate with the richer body is kept and the other is dropped

#### Scenario: Procedure exemption survives the union

- GIVEN a `Procedure`-type candidate that would otherwise be dropped as a
  source-title twin
- WHEN twin-drop runs per run and the union is merged
- THEN the `Procedure` candidate is retained, re-derived by the same
  deterministic rule used for single-run extraction, never by a judge prompt
  clause

### Requirement: Judge Selection Over a Closed Candidate List

The judge MUST be given the complete merged candidate list and source text
in one call, and MUST only select from that closed list — it MUST NOT
introduce candidates absent from the union.

#### Scenario: Judge cannot fabricate a candidate

- GIVEN a merged candidate list of N items
- WHEN the judge reply references an item not present in that list
- THEN the reference is discarded before the selection is admitted and does not
  appear in the final selected set

### Requirement: Judge Failure Fails Closed to the Backstopped Union

WHEN the judge call raises `OllamaError`, returns an empty reply, or returns
a reply that fails parsing/validation, the system MUST NOT discard the
extraction work. It MUST fall back to the full merged union, truncated by
the backstop cap, MUST flag this degrade in the `ExtractionReport`, and MUST
emit a note to stderr. Extraction MUST NOT raise in this path.

#### Scenario: Judge OllamaError degrades to backstopped union

- GIVEN a merged union of valid candidates and a judge call that raises
  `OllamaError`
- WHEN extraction completes
- THEN the returned objects are the merged union truncated by the backstop
  cap, the report records the judge failure, and a note appears on stderr

#### Scenario: Unparseable judge reply degrades the same way

- GIVEN a judge reply that is not valid JSON
- WHEN extraction completes
- THEN the outcome is identical to the `OllamaError` degrade path

#### Scenario: Valid selection admitting zero objects degrades the same way

- GIVEN a non-empty merged union and a judge reply that is valid in shape
  but whose admitted set — after closed-candidate-list matching and
  Procedure re-admission — is empty
- WHEN extraction completes
- THEN the returned objects are the merged union truncated by the backstop
  cap, the report records the degrade with a status distinct from judge
  success, and a note appears on stderr; extraction MUST NOT return zero
  objects while the merged union is non-empty (the extraction prompt's own
  "fewer, never zero" principle, enforced deterministically at the
  pipeline level). Measured motivation: on `TS3005a.transcript` both runs
  collapse to one umbrella Event, the judge rejects it, and without this
  floor the pipeline returned `[]` with `judge_status="ok"`.

### Requirement: Backstop Cap Applied Once, After Judge Selection

The system MUST apply a fixed backstop cap of 12 objects exactly once, after
judge selection (or after the failure degrade), never before. The cap MUST
NOT be user-configurable.

#### Scenario: Judge selection under the cap is untouched

- GIVEN a judge-selected set of 7 objects
- WHEN the backstop is applied
- THEN all 7 objects are kept unchanged

#### Scenario: Pathological judge output is bounded

- GIVEN a judge-selected (or failure-degraded) set of more than 12 objects
- WHEN the backstop is applied
- THEN no more than 12 objects are returned

### Requirement: Run and Judge Bookkeeping on the Extraction Report

`ExtractionReport` MUST record, alongside existing fields, whether the union
path or judge-only path was used, whether the judge call succeeded, and the
pre-backstop and post-backstop candidate counts.

#### Scenario: Report distinguishes judge success from degrade

- GIVEN one ingest where the judge succeeds and another where it fails
- WHEN each `ExtractionReport` is inspected
- THEN the judge-outcome field differs between the two reports

### Requirement: Opt-Out Configuration Flag

The system MUST expose a configuration flag that disables the union+judge
pipeline and restores the single-run, single-cap path byte-for-byte. The
flag's default MUST be enabled (union+judge ON).

#### Scenario: Flag disabled restores single-run behavior

- GIVEN the union+judge flag set to disabled
- WHEN `openkos ingest <path>` runs on a source below the chunk threshold
- THEN exactly one extraction call is made and no judge call occurs
