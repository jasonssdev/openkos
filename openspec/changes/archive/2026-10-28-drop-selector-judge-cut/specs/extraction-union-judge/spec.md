# Delta for extraction-union-judge

## MODIFIED Requirements

### Requirement: Judge Selection Over a Closed Candidate List

WHEN the selector judge is enabled (see "The Selector Judge Is Opt-In"), the
judge MUST be given the complete merged candidate list and source text
in one call, and MUST only select from that closed list — it MUST NOT
introduce candidates absent from the union.

#### Scenario: Judge cannot fabricate a candidate

- GIVEN a merged candidate list of N items
- WHEN the judge reply references an item not present in that list
- THEN the reference is discarded before the selection is admitted and does not
  appear in the final selected set

#### Scenario: Selector judge off makes no judge call

- GIVEN `selector_judge` is off (the default)
- WHEN extraction completes
- THEN no judge call is made and every candidate that survived the pre-judge
  ceiling goes on to the backstop

### Requirement: Judge Failure Fails Closed to the Backstopped Union

WHEN the selector judge is enabled and its call raises `BackendError`, returns an empty reply, or returns
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
judge selection (or after the empty-selection degrade), never before. WHEN the
selector judge is off, the cap MUST be applied to the merged set that survived
the pre-judge ceiling of 24, positionally; the judge status is then `skipped`,
which is a status the cap applies under. The cap
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

#### Scenario: Selector judge off caps the ceiling-bounded set positionally

- GIVEN `selector_judge` is off and a merged union of 25 distinct candidates
- WHEN extraction completes
- THEN the pre-judge ceiling keeps 24, no judge call is made, and the backstop
  keeps the first 20

#### Scenario: Judge unavailable keeps the unranked set whole

- GIVEN a merged union of more than 20 candidates and a judge that failed on
  both attempts
- WHEN extraction completes
- THEN the backstop is not applied and all candidates that survived the
  pre-judge ceiling are returned

### Requirement: Run and Judge Bookkeeping on the Extraction Report

`ExtractionReport` MUST record, alongside existing fields, whether the union
path or judge-only path was used, whether the judge call succeeded, and the
pre-backstop and post-backstop candidate counts. WHEN the selector judge did
not run, the judge status MUST be `skipped`.

#### Scenario: Report distinguishes judge success from degrade

- GIVEN one ingest where the judge succeeds and another where it fails
- WHEN each `ExtractionReport` is inspected
- THEN the judge-outcome field differs between the two reports

### Requirement: Opt-Out Configuration Flag

The system MUST expose a configuration flag, `union_judge`, that disables the
union-of-runs extractor and restores the single-run, single-cap path
byte-for-byte. The flag's default MUST be enabled. The flag governs the
union-of-runs extractor only; whether the selector judge runs under it is
governed by `selector_judge`.

#### Scenario: Flag disabled restores single-run behavior

- GIVEN the union+judge flag set to disabled
- WHEN `openkos ingest <path>` runs on a source below the chunk threshold
- THEN exactly one extraction call is made and no judge call occurs

### Requirement: Judge Re-Admission Set Extended to Person/Organization (Additive Only)

WHEN the selector judge runs, the judge re-admission path (the deterministic
step that restores a candidate the judge dropped, applied AFTER judge
selection) MUST use a
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

WHEN the selector judge runs, judge re-admission of `Person`/`Organization`
candidates MUST only apply to
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

### Requirement: Extraction Exposes An Upper Bound On Its Calls

`extraction.concept` MUST expose `max_extraction_calls(source_text, *,
union_judge, transcript_decisions, selector_judge=False)`, a pure function that returns a number of
chat calls no extraction of `source_text` can exceed, whatever the model
replies and whatever title the source is given. It MUST count each mechanism at
its maximum: two calls per window on a chunked source (the window's retry), two
below the threshold (the union path's two passes, or the single-run path's
retry of an empty reply), one re-ask, and on the union path one participant
pass for a meeting-shaped source, two calls per window (one window below the
threshold) for the decision pass when `transcript_decisions` is on and the
source is meeting-shaped, and, only when `selector_judge` is on,
`JUDGE_ATTEMPTS` judge calls. Because the title
decides whether a source reads as a meeting, which moves the chunking threshold
and the meeting-only passes, it MUST return the larger of the two readings.
Blank text MUST return 0. It MUST never be below `estimate_extraction_calls`
for the same text and settings.

#### Scenario: Every mechanism at its maximum on a short source

- GIVEN a short source, the union path, the decision pass and the selector
  judge on
- WHEN the bound is computed
- THEN it is two passes, one re-ask, one participant pass, two decision calls
  and every judge attempt

#### Scenario: The selector judge off removes its attempts from the bound

- GIVEN the same source with `selector_judge` off
- WHEN the bound is computed
- THEN it is smaller by exactly `JUDGE_ATTEMPTS`

#### Scenario: Adversarial replies stay within the bound

- GIVEN a chunked source and replies that fail each window's first attempt and
  never give the judge or the decision pass a usable reply
- WHEN `extract_concept_union` runs
- THEN the calls made are at most the bound and at least two per window

## ADDED Requirements

### Requirement: The Selector Judge Is Opt-In

The system MUST expose a boolean configuration key `selector_judge` whose
packaged default is `false`. WHEN it is off, `extract_concept_union` MUST make
no judge call, MUST report the judge status `skipped`, and MUST apply the
pre-judge ceiling of 24 merged candidates and then the positional backstop of
20, so the extractor keeps the ceiling-bounded set cut positionally. WHEN it is
`true`, the selector judge MUST run exactly as specified by the judge
requirements of this capability. The key MUST be validated as a boolean and
never coerced: a non-boolean value (including an integer) MUST be refused with
an error naming the key, and an explicit null MUST fall back to the default.
The key MUST have no effect when `union_judge` is off, because the single-run
path has no judge.

#### Scenario: The default makes no judge call

- GIVEN a workspace whose `openkos.yaml` does not name `selector_judge`
- WHEN `openkos ingest <path>` extracts a source below the chunk threshold
- THEN exactly two extraction calls are made, no judge call is made, and the
  report's judge status is `skipped`

#### Scenario: The key turns the judge back on

- GIVEN `selector_judge: true`
- WHEN the same source is extracted
- THEN the judge call is made and the judge's selection is applied before the
  backstop, exactly as before this key existed

#### Scenario: A non-boolean value is refused

- GIVEN `selector_judge: 1` in `openkos.yaml`
- WHEN the configuration is read
- THEN reading fails with an error naming `selector_judge`
