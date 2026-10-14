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

WHEN the judge call raises `BackendError`, returns an empty reply, or returns
a reply that fails parsing/validation, the system MUST NOT discard the
extraction work. It MUST fall back to the full merged union, left unranked
and without the backstop cap (see "Backstop Cap Applied Once, After Judge
Selection"), MUST flag this degrade in the `ExtractionReport`, and MUST
emit a note to stderr. Extraction MUST NOT raise in this path.

#### Scenario: Judge BackendError degrades to the unranked union

- GIVEN a merged union of valid candidates and a judge call that raises
  `BackendError`
- WHEN extraction completes
- THEN the returned objects are the full merged union (bounded only by the
  pre-judge ceiling), the report records the judge failure, and a note
  appears on stderr

#### Scenario: Unparseable judge reply degrades the same way

- GIVEN a judge reply that is not valid JSON
- WHEN extraction completes
- THEN the outcome is identical to the `BackendError` degrade path

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

The system MUST apply a fixed backstop cap of 20 objects exactly once, after
judge selection (or after the empty-selection degrade), never before. The cap
MUST NOT be user-configurable. WHEN the judge is unavailable (both judge
attempts failed, so nothing ranked the candidates), the system MUST NOT apply
the cap, because a positional cut on an unranked set discards by arrival
order; that set remains bounded by the pre-judge ceiling of 24 merged
candidates.

#### Scenario: Judge selection under the cap is untouched

- GIVEN a judge-selected set of 7 objects
- WHEN the backstop is applied
- THEN all 7 objects are kept unchanged

#### Scenario: Pathological judge output is bounded

- GIVEN a judge-selected (or empty-selection-degraded) set of more than 20
  objects
- WHEN the backstop is applied
- THEN no more than 20 objects are returned

#### Scenario: Judge unavailable keeps the unranked set whole

- GIVEN a merged union of more than 20 candidates and a judge that failed on
  both attempts
- WHEN extraction completes
- THEN the backstop is not applied and all candidates that survived the
  pre-judge ceiling are returned

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

- GIVEN a `Person` candidate on a meeting-shaped source, which the judge's
  selection dropped
- WHEN judge re-admission runs after selection
- THEN the candidate is added back to the final set, deterministically, not
  via any judge prompt clause, and no context-anchor check gates this
  addition

#### Scenario: A Person title-twin of the source is still dropped

- GIVEN a `Person` candidate whose title is a twin of the source title
- WHEN the twin-drop deletion rule runs
- THEN the candidate is dropped; `Person` is NOT exempt from twin-drop, only
  `Procedure` is

#### Scenario: A meeting-titled Person is still dropped by framing removal

- GIVEN a `Person` candidate titled after the meeting itself (a framing
  stub, the shape the eval harness measures)
- WHEN `_drop_framing_objects` runs
- THEN the candidate is dropped; `Person` is NOT exempt from framing removal

#### Scenario: Procedure behavior is unchanged at all three sites

- GIVEN a `Procedure` candidate that would trigger twin-drop, framing
  removal, or judge re-admission
- WHEN each of the three sites evaluates that candidate
- THEN the outcome is identical to current behavior; only the judge
  re-admission site gained new eligible types

### Requirement: Judge Re-Admission Scoped to Meeting-Shaped Sources

Judge re-admission of `Person`/`Organization` candidates MUST only apply to
transcript/meeting-shaped sources, using the same shape test as
`_is_meeting_shaped` — the source's TITLE or its CONTENT shape. A
non-meeting-shaped source (for example a technical article that merely
mentions a person's name, with no speaker-turn structure) MUST NOT produce
a judge-re-admitted `Person`/`Organization` candidate.

#### Scenario: Meeting transcript re-admits a judge-dropped participant

- GIVEN a meeting-shaped source and a `Person` candidate that the judge
  dropped
- WHEN judge re-admission runs
- THEN the candidate is added back to the final set

#### Scenario: Non-meeting source does not re-admit a participant

- GIVEN a technical-article source (not meeting-shaped) and a `Person`
  candidate the judge dropped
- WHEN judge re-admission runs
- THEN the candidate is NOT re-admitted through this path

### Requirement: Transcript Decision Pass Is Opt-In And Default-Off

The workspace configuration MUST accept a boolean key `transcript_decisions`
whose default is `false`. WHILE it is `false`, ingest MUST NOT spend any
decision-pass call and its output MUST be byte-identical to the output
without this capability.

#### Scenario: The key is off

- GIVEN a meeting-shaped source and `transcript_decisions` unset
- WHEN the source is ingested
- THEN no decision-pass call is made and the written objects are identical
  to those produced before this capability existed

### Requirement: The Decision Pass Runs Per Window On Meeting-Shaped Sources

WHEN `transcript_decisions` is `true` and the source is meeting-shaped (the
same predicate that gates participant capture), the system MUST run the
decision pass once per chunk window, or once over the whole text when the
source is at or below the chunk threshold. Each call MUST ask only for the
decisions the participants reached in that window, and MUST return items of
shape `{subject, value, evidence}`. An empty array MUST be a valid answer.

#### Scenario: A chunked transcript is asked window by window

- GIVEN a meeting-shaped source that splits into three windows
- WHEN the pass runs
- THEN three calls are made, each carrying only its own window

#### Scenario: A non-meeting source is not asked

- GIVEN `transcript_decisions` is `true` and a source that is not
  meeting-shaped
- WHEN the source is ingested
- THEN no decision-pass call is made

### Requirement: Pass Items Are Admitted Fail-Closed

An item MUST be dropped unless `subject`, `value` and `evidence` are
non-empty strings, `value` has at least three words, and `evidence` is
quoted verbatim in the window that produced it (casefold and
whitespace-collapse normalization, as for the existing quoted-verbatim check). Surviving items MUST be deduplicated by
normalized subject across windows, the first occurrence winning, and MUST be
capped per source by a fixed bound (25) applied after deduplication, in reply
and window order.

#### Scenario: Paraphrased evidence is dropped

- GIVEN an item whose evidence line does not appear in its window
- WHEN the pass admits items
- THEN that item produces no Decision

#### Scenario: A value under three words is dropped

- GIVEN an item whose value is "Two buttons" and whose evidence is verbatim
- WHEN the pass admits items
- THEN that item produces no Decision

#### Scenario: The same subject in two windows yields one Decision

- GIVEN two windows each returning an item with the subject "Remote case"
  (differing only in case or spacing)
- WHEN the pass admits items
- THEN exactly one Decision is produced, from the first window

### Requirement: A Pass Decision Body Is Composed Deterministically

Each admitted item MUST become a candidate of type `Decision` whose title is
derived from the value (the value as a short statement, without its closing
punctuation, at most 100 characters, cut at a word boundary), whose
description is `<subject>: <value>`, and whose body is composed only from the value and the quoted evidence
line. The body MUST NOT contain any other text of the window.

The title MUST NOT be the bare subject. Ingest attaches a candidate to an
existing concept of the same type and normalized title, so two different
decisions about one subject in two meetings would otherwise merge into one
object and the pair that decision-revision detection needs would never exist.
The same decision stated again from the same source keeps its title.

#### Scenario: Two decisions on one subject stay two objects

- GIVEN a stored Decision from one meeting whose value is "Use a solar cell."
- WHEN a later meeting yields a Decision about the same subject with the
  value "Use a lithium cell."
- THEN ingest creates a second Decision rather than attaching to the first

#### Scenario: The body carries the value and the quote only

- GIVEN an admitted item with value "Use a regular chip." and evidence
  "we'll go with the regular chip"
- WHEN the Decision is composed
- THEN its body contains the value and the evidence and no other transcript
  text

### Requirement: Pass Decisions Replace The Main Pass's Decisions Fail-Closed

WHEN the pass yields at least one Decision for a source, those Decisions
MUST replace the main extraction pass's Decision candidates for that source
(removed before the judge runs); candidates of other types MUST be untouched.
The pass Decisions MUST join the retained set after the judge and its
positional backstop, so the backstop never drops a verified pass Decision;
they are bounded only by their own cap. WHEN the pass yields none, or
fails because of a backend error or an unparseable reply, the main pass's
Decisions MUST be kept unchanged and the failure MUST be reported without
raising.

#### Scenario: The pass yields Decisions

- GIVEN the main pass proposed one topic-bundle Decision and a Person
- WHEN the decision pass admits two Decisions
- THEN the source yields the two pass Decisions and the Person, and not the
  topic-bundle Decision

#### Scenario: The pass fails

- GIVEN the backend raises during the decision pass
- WHEN the source is ingested
- THEN the main pass's Decisions are kept, the failure is reported, and
  ingest does not raise
