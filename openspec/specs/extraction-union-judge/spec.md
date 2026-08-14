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

### Requirement: Merged-List Twin-Drop and Richer-Body Merge

Source-title twins MUST be dropped (respecting the existing `Procedure`
exemption) from the MERGED union, not from each run's contribution
separately — the rule's single-object floor reads the whole candidate set,
so a run evaluated alone can floor back in a twin the union does not
qualify for. WHEN merging the union, a `(type, normalized-title)` collision
between candidates from different runs MUST be resolved by keeping the
candidate with the richer body/description, not by first-occurrence order.

#### Scenario: Twin-drop applies to the merged list

- GIVEN one run whose output contains a title-twin of the source
- WHEN the union is merged and a non-twin exists anywhere across it
- THEN the twin is dropped from the merged candidate list, independent of
  which run emitted it

#### Scenario: A twin kept by one run's floor does not survive the union

- GIVEN a run whose ONLY candidate is a title-twin of the source, and
  another run contributing a genuine non-twin
- WHEN the union is merged
- THEN the twin is dropped, because the merged list contains a non-twin

#### Scenario: The floor survives on the union path

- GIVEN every candidate in the merged union is a title-twin of the source
- WHEN twin-drop runs on that merged list
- THEN the candidates are kept unchanged, so a genuinely single-subject
  source never degrades to zero objects

#### Scenario: Richer body wins on collision

- GIVEN two candidates from different runs with the same `(type,
  normalized-title)` but different body lengths
- WHEN the union is merged
- THEN the candidate with the richer body is kept and the other is dropped

#### Scenario: Procedure exemption survives the union

- GIVEN a `Procedure`-type candidate that would otherwise be dropped as a
  source-title twin
- WHEN twin-drop runs on the merged union
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

### Requirement: Judge Re-Admission Set Extended to Person/Organization (Additive Only)

The judge re-admission path (the deterministic step that restores a
candidate the judge dropped, applied AFTER judge selection) MUST use a
distinct set covering `Procedure`, `Person`, and `Organization`. This set
MUST NOT be used at either deletion site (source-title twin-drop or
framing-object drop): those deletion predicates MUST remain scoped to
`Procedure` only, byte-identical to their current behavior. Deletion and
additive re-admission are different consumers and MUST NOT share one
predicate.

#### Scenario: Judge-dropped Person on a meeting-shaped source is re-admitted

- GIVEN a `Person` candidate on a meeting-shaped source that carries a valid
  participant anchor, which the judge's selection dropped
- WHEN judge re-admission runs after selection
- THEN the candidate is added back to the final set, deterministically, not
  via any judge prompt clause

#### Scenario: A Person title-twin of the source is still dropped

- GIVEN a `Person` candidate whose title is a twin of the source title
- WHEN the twin-drop deletion rule runs
- THEN the candidate is dropped; `Person` is NOT exempt from twin-drop, only
  `Procedure` is

#### Scenario: A meeting-titled Person is still dropped by framing removal

- GIVEN a `Person` candidate titled after the meeting itself (a framing
  stub, the shape measured in #522/#533)
- WHEN `_drop_framing_objects` runs
- THEN the candidate is dropped; `Person` is NOT exempt from framing removal

#### Scenario: Procedure behavior is unchanged at all three sites

- GIVEN a `Procedure` candidate that would trigger twin-drop, framing
  removal, or judge re-admission
- WHEN each of the three sites evaluates that candidate
- THEN the outcome is identical to current behavior; only the judge
  re-admission site gained new eligible types

### Requirement: Stub Rejection at Judge Re-Admission

At the judge re-admission step, a `Person` or `Organization` candidate MUST
NOT be re-admitted unless it carries a minimal context anchor beyond its
name: a meeting role, an affiliation, or a relation (for example
`spoke_in`, `member_of`). A name-only candidate is a stub and MUST be
discarded, not re-admitted. This anchor check applies ONLY to the additive
re-admission step, never to the deletion sites.

#### Scenario: Name-only candidate is not re-admitted

- GIVEN a `Person` candidate that the judge dropped, whose only attribute
  is a name, with no role, affiliation, or relation
- WHEN judge re-admission runs
- THEN the candidate remains dropped and does not appear in the final set

#### Scenario: Candidate with a meeting-role anchor is re-admitted

- GIVEN a `Person` candidate that the judge dropped, carrying a meeting
  role (for example "chair") alongside its name
- WHEN judge re-admission runs
- THEN the candidate is added back to the final set

### Requirement: Judge Re-Admission Scoped to Meeting-Shaped Sources

Judge re-admission of `Person`/`Organization` candidates MUST only apply to
transcript/meeting-shaped sources, using the same shape test as
`_MEETING_SHAPED_TITLE_RE`. A non-meeting-shaped source (for example a
technical article that merely mentions a person's name) MUST NOT produce a
judge-re-admitted `Person`/`Organization` candidate.

#### Scenario: Meeting transcript re-admits a judge-dropped participant

- GIVEN a meeting-shaped source and a `Person` candidate that the judge
  dropped and that satisfies the participant-anchor requirement
- WHEN judge re-admission runs
- THEN the candidate is added back to the final set

#### Scenario: Non-meeting source does not re-admit a participant

- GIVEN a technical-article source (not meeting-shaped) and a `Person`
  candidate the judge dropped
- WHEN judge re-admission runs
- THEN the candidate is NOT re-admitted through this path
