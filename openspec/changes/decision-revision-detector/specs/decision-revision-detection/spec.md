# Delta for Decision Revision Detection

## ADDED Requirements

### Requirement: Post-Hoc Subject Pass Over Decisions Only

The system MUST derive `subject`, `value` and one `evidence` quote for a
Decision through a separate, post-hoc LLM call over that Decision's own
`title`/`description`/`body`, parsed fail-closed through `llm/parsing.py`.
This pass MUST NOT read or modify any other concept, MUST NOT run over any
non-Decision object, and MUST NOT change the shared extraction/
classification prompt used at `ingest` time.

#### Scenario: Subject pass runs only over Decisions

- GIVEN a bundle containing Decisions, Concepts, and Entities
- WHEN the subject pass runs
- THEN it is called once per Decision needing a subject, and never for a
  Concept or an Entity

#### Scenario: A well-formed reply yields subject, value and evidence

- GIVEN a Decision whose body supports a clear subject
- WHEN the subject pass parses a well-formed JSON reply
- THEN the Decision's `subject`, `value`, and `evidence` are recorded as
  given by the reply

### Requirement: Verbatim Evidence Quote Check

An `evidence` quote returned by the subject pass MUST be checked against
the Decision's body. WHEN the quote does not appear verbatim in the body,
the `evidence` field MUST be dropped for that Decision, and the `subject`
and `value` fields MUST still be recorded (the subject stands even when
its evidence is dropped).

#### Scenario: A verbatim quote is kept

- GIVEN a subject-pass reply whose `evidence` string appears verbatim in
  the Decision's body
- WHEN the reply is validated
- THEN the evidence is recorded alongside the subject and value

#### Scenario: A non-verbatim quote is dropped without dropping the subject

- GIVEN a subject-pass reply whose `evidence` string does not appear
  verbatim anywhere in the Decision's body
- WHEN the reply is validated
- THEN the evidence field is dropped, and the subject and value are still
  recorded for that Decision

### Requirement: Subject Cache Keyed By Concept Id, Body Digest And Prompt Version

Every Decision's subject-pass result MUST be stored in a derived cache
inside `.openkos/findings.db`, keyed by the Decision's concept id, a digest
of its body, and the subject prompt's version constant. The subject pass
MUST run lazily, only when the detector needs a Decision's subject and the
cache has no entry matching all three key components (a cache miss). The
subject pass MUST NOT run during `openkos ingest`, and `ingest`'s behavior
and cost gate MUST be unaffected by this cache's existence.

#### Scenario: A cache hit serves the stored subject with no LLM call

- GIVEN a Decision whose subject was cached on a prior `revisions` run and
  whose body and the prompt version are unchanged
- WHEN `revisions` runs again
- THEN that Decision's subject is served from the cache and no subject-pass
  LLM call is made for it

#### Scenario: An edited body invalidates the cache entry

- GIVEN a Decision whose body changes after its subject was cached
- WHEN `revisions` runs again
- THEN the stale entry is a cache miss, and the subject pass runs again for
  that Decision

#### Scenario: ingest never touches the subject cache

- GIVEN a bundle with Decisions whose subjects have never been computed
- WHEN `openkos ingest` runs
- THEN no subject-pass LLM call is made and no subject-cache row is written

### Requirement: A Malformed Subject-Pass Reply Does Not Abort The Run

WHEN a subject-pass call for one Decision fails to produce a reply that
parses fail-closed, that Decision MUST be left without a subject for this
run, MUST NOT populate the subject cache, and MUST NOT abort the detection
run for any other Decision.

#### Scenario: One Decision's malformed reply does not block the others

- GIVEN two Decisions needing a subject pass, where one call returns a
  malformed reply and the other returns a well-formed reply
- WHEN the subject pass runs for both
- THEN the well-formed Decision's subject is recorded and cached, the
  malformed Decision is left without a subject and without a cache entry,
  and the run continues

### Requirement: Decision Event Date Resolution

The system MUST provide a helper that resolves a Decision's event date by
walking `bundle/provenance.provenance_source_ancestors` to the Decision's
reached Sources and reading each one's `event_date` via `okf.read_event_date`.
The helper MUST return exactly one of: a single resolved date (every reached
Source agrees, or only one Source is reached and it carries a valid date);
`missing` (any reached Source has no `event_date` or a malformed one);
`multiple` (the reached Sources carry more than one distinct valid date);
or `none-reached` (provenance resolves to no Source at all).

#### Scenario: A single-source Decision resolves one date

- GIVEN a Decision whose provenance reaches exactly one Source with a valid
  `event_date`
- WHEN the helper resolves its date
- THEN it returns that single date

#### Scenario: A Source with no event_date resolves as missing

- GIVEN a Decision whose provenance reaches a Source with no `event_date`
- WHEN the helper resolves its date
- THEN it returns `missing`

#### Scenario: Provenance reaching multiple distinct dates resolves as multiple

- GIVEN a merged Decision whose provenance reaches two Sources with two
  different valid `event_date` values
- WHEN the helper resolves its date
- THEN it returns `multiple`

#### Scenario: No reachable Source resolves as none-reached

- GIVEN a Decision whose provenance resolves to no Source
- WHEN the helper resolves its date
- THEN it returns `none-reached`

### Requirement: Candidate Eligibility Exclusions Applied Before Counting

Candidate generation MUST consider only Decision pairs whose Source sets
(via provenance) are disjoint. Before any candidate is counted or capped,
candidate generation MUST exclude: any pair joined, in either direction, by
a `RESOLUTION_RELATION_TYPES` edge (`supersedes`, `reconciled_with`, or
`revises`); any pair where either Decision is deprecated; and any pair
where either Decision is confidential, unless `--include-confidential` is
passed.

#### Scenario: Decisions sharing a Source form no candidate

- GIVEN two Decisions whose provenance reaches an overlapping Source
- WHEN candidate generation runs
- THEN no candidate pair is formed between them

#### Scenario: A resolved pair is excluded before the count

- GIVEN two Decisions already joined by a `revises` edge (or, separately,
  `supersedes` or `reconciled_with`)
- WHEN candidate generation runs
- THEN that pair is excluded before the pre-cap total is computed

#### Scenario: A deprecated Decision is excluded unconditionally

- GIVEN a Decision marked deprecated, otherwise eligible to pair with
  another Decision
- WHEN candidate generation runs, with or without `--include-confidential`
- THEN no candidate pair involves the deprecated Decision

#### Scenario: A confidential Decision is excluded by default and included with the flag

- GIVEN a confidential Decision, otherwise eligible to pair
- WHEN candidate generation runs without `--include-confidential`
- THEN no candidate pair involves it
- WHEN candidate generation runs with `--include-confidential`
- THEN it becomes eligible for candidate pairing, subject to every other
  exclusion

### Requirement: Candidate Ranking And Caps

Eligible candidate pairs MUST be blocked by the semantic similarity of the
two Decisions' embeddings: the cosine similarity of their vectors MUST be at
or above a named threshold for the pair to survive. For each Decision, at
most the 5 most similar eligible counterpart Decisions MUST be retained.
Across the whole run, the total candidate count MUST be capped at 200,
applied after per-Decision ranking. WHEN the cap truncates the pre-cap
total, the run MUST produce a truncation notice naming how many candidates
are shown out of the pre-cap total.

#### Scenario: Per-Decision ranking keeps the top 5

- GIVEN a Decision with 8 eligible, similarity-ranked counterpart Decisions
- WHEN candidate generation runs
- THEN at most 5 candidate pairs are retained for that Decision, the 5 most
  similar

#### Scenario: The global cap truncates with a notice

- GIVEN a run whose pre-cap candidate total exceeds 200
- WHEN candidate generation runs
- THEN exactly 200 candidates are judged and a truncation notice states how
  many are shown out of the pre-cap total

#### Scenario: Under the cap, no truncation notice appears

- GIVEN a run whose pre-cap candidate total is at or below 200
- WHEN candidate generation runs
- THEN every eligible candidate is judged and no truncation notice appears

#### Scenario: A Decision without an embedding forms no candidate and is counted

- GIVEN a Decision with no embedding vector available
- WHEN candidate generation runs
- THEN no candidate pair involves that Decision, and it is counted
  separately from the pairs eligible for similarity ranking

### Requirement: Judge Verdict Vocabulary And Reply Shape

For each candidate pair, the system MUST make one fail-closed JSON judge
call whose parsed result carries exactly one of `REVERSES`, `REFINES`,
`REAFFIRMS`, or `UNRELATED`, a confidence, a rationale, and one quote from
each side. The reply schema MUST NOT define any field for direction or
ordering; a reply carrying an unexpected field claiming an order or
direction MUST be ignored by the parser.

#### Scenario: A well-formed reply yields one of the four verdicts

- GIVEN a candidate pair and a well-formed judge reply
- WHEN the reply is parsed
- THEN the result carries exactly one of `REVERSES`, `REFINES`,
  `REAFFIRMS`, or `UNRELATED`, with a confidence, a rationale, and one
  quote from each side

#### Scenario: An extra field claiming a direction is ignored

- GIVEN a judge reply whose JSON includes an additional field that names an
  order or direction between the two sides
- WHEN the reply is parsed
- THEN the parsed verdict carries no direction value from that field, and
  the pair's persisted direction is unaffected by it

### Requirement: Candidate Presentation Order Given To The Judge

WHEN both Decisions in a candidate pair resolve a known direction (per the
event-date requirement below), the pair MUST be presented to the judge
earlier-Decision-first, each side labelled with its resolved date. WHEN
direction is not known, the pair MUST be presented in concept-id order,
both sides labelled "order unknown".

#### Scenario: A dated pair is presented earlier-first with dates

- GIVEN a candidate pair whose two Decisions resolve distinct, known dates
- WHEN the pair is presented to the judge
- THEN the earlier Decision is presented first, and both sides carry their
  resolved dates

#### Scenario: An undated pair is presented in id order, labelled unknown

- GIVEN a candidate pair where direction is not known
- WHEN the pair is presented to the judge
- THEN the two sides are presented in concept-id order, both labelled
  "order unknown"

### Requirement: Direction Comes Only From Resolved Source event_date

A candidate pair's direction MUST be recorded as known, naming the later
Decision, only when both Decisions individually resolve to exactly one
event date (neither `missing`, `multiple`, nor `none-reached`) AND those
two resolved dates are not equal. In every other case — either side
missing, multiple, or none-reached, or the two resolved dates being equal —
direction MUST be recorded as unknown. No field in the judge's reply MUST
be read to set direction.

#### Scenario: Two distinct resolved dates yield a known direction

- GIVEN a pair whose two Decisions each resolve exactly one event date, and
  those dates differ
- WHEN the pair's direction is determined
- THEN direction is known, and the later-dated Decision is named as later

#### Scenario: Equal resolved dates yield an unknown direction

- GIVEN a pair whose two Decisions each resolve the same single event date
- WHEN the pair's direction is determined
- THEN direction is recorded as unknown

#### Scenario: A missing, multiple, or none-reached date yields an unknown direction

- GIVEN a pair where at least one Decision's event date resolves to
  `missing`, `multiple`, or `none-reached`
- WHEN the pair's direction is determined
- THEN direction is recorded as unknown, regardless of what the judge's
  reply says

### Requirement: An Undirected Change Verdict Is Untyped

A judged pair carrying a `REVERSES` or `REFINES` verdict whose direction is
unknown (per the direction requirement above) MUST be classified as an
untyped CHANGE, and the system MUST NOT infer a relation type
(`supersedes` vs. `revises`) for it from the judge's verdict alone. This
classification MUST be computed only from the pair's already-recorded
verdict and its direction (itself computed only from resolved
`event_date`s, per the requirement above) — never from a new field the
judge is asked to supply, and the four-value judge verdict vocabulary
(`REVERSES`/`REFINES`/`REAFFIRMS`/`UNRELATED`) MUST remain exactly as the
"Judge Verdict Vocabulary And Reply Shape" requirement defines it. An
undirected `REVERSES`/`REFINES` finding otherwise meeting the actionability
rule (confidence and both quotes verified) MUST still be surfaced to the
human as actionable — only its relation type is withheld, not the finding
itself.

#### Scenario: An undirected REVERSES is an untyped change

- GIVEN a candidate pair whose direction is unknown and whose judged verdict
  is `REVERSES`
- WHEN the finding is classified
- THEN it is recorded as an untyped change, and no relation type is
  inferred from the verdict for it

#### Scenario: An undirected REFINES is an untyped change

- GIVEN a candidate pair whose direction is unknown and whose judged verdict
  is `REFINES`
- WHEN the finding is classified
- THEN it is recorded as an untyped change, and no relation type is
  inferred from the verdict for it

#### Scenario: A directed REVERSES or REFINES is unaffected

- GIVEN a candidate pair whose direction is known and whose judged verdict
  is `REVERSES` or `REFINES`
- WHEN the finding is classified
- THEN it is NOT an untyped change, and its relation type is inferred
  exactly as before (`supersedes` for `REVERSES`, `revises` for `REFINES`)

#### Scenario: An untyped change remains actionable

- GIVEN an undirected `REVERSES` or `REFINES` finding whose confidence and
  both quotes meet the existing actionability rule
- WHEN the finding is evaluated for actionability
- THEN it is still actionable, unchanged by its direction or by being an
  untyped change

### Requirement: Fail-Closed Judge Parsing With Partial Batch On Failure

A judge reply that fails to parse fail-closed MUST be treated as a
malformed-reply degrade for that pair only, and MUST NOT abort judging the
remaining candidates in the batch. The run MUST report, at the end, how
many pairs were judged successfully and how many degraded.

#### Scenario: One malformed reply degrades without aborting the batch

- GIVEN a batch of candidate pairs where one judge reply is malformed and
  the rest are well-formed
- WHEN the batch is judged
- THEN the malformed pair degrades and every other pair is still judged and
  persisted

### Requirement: `openkos revisions` Is Labelled Experimental

The system MUST provide a CLI verb `openkos revisions` that is labelled
experimental in `--help`, and MUST print one stderr line, on every
invocation, stating that the detector's accuracy is unmeasured.

#### Scenario: --help labels the verb experimental

- GIVEN `openkos revisions --help`
- WHEN the help text is printed
- THEN it labels the command experimental

#### Scenario: Every run states unmeasured quality on stderr

- GIVEN any invocation of `openkos revisions`
- WHEN the command runs
- THEN stderr includes one line stating the detector's quality is
  unmeasured

### Requirement: Zero-LLM Probe Precedes Each Cost Gate

Before any subject-pass LLM call, and again before any judge LLM call, the
system MUST compute and display an exact count for that phase — the number
of subject-cache misses, then the number of candidate pairs to judge —
using zero LLM calls to produce either count.

#### Scenario: The subject-pass count is exact and LLM-free

- GIVEN a bundle with a known number of Decisions whose subject cache
  entry is missing or stale
- WHEN `revisions` computes its subject-pass probe
- THEN the displayed count exactly matches that number, and no subject-pass
  LLM call has yet been made

#### Scenario: The pair-judgment count is exact and LLM-free

- GIVEN a resolved candidate plan with a known judgeable pair count
- WHEN `revisions` computes its pair-judgment probe
- THEN the displayed count exactly matches the candidate plan's judged
  count, and no judge LLM call has yet been made

### Requirement: Two Sequential Exact Cost Gates

`openkos revisions` MUST gate proceeding past each of the two LLM-spending
phases — the subject pass, then pair judgment — behind confirmation, in
that order: subject calls are gated and made (for cache misses) before
candidate generation runs, and pair judgments are gated and made only
after candidates exist. Declining a gate MUST stop before any LLM call in
that phase is made and MUST NOT prevent a later, separate `revisions`
invocation from being offered the same gate again.

#### Scenario: Declining the subject-pass gate makes no subject-pass call

- GIVEN a bundle with subject-cache misses
- WHEN the operator declines the subject-pass gate
- THEN no subject-pass LLM call is made and no candidates are generated

#### Scenario: Declining the pair-judgment gate makes no judge call

- GIVEN a resolved candidate plan
- WHEN the operator declines the pair-judgment gate
- THEN no judge LLM call is made, and no revision finding is persisted for
  this run's candidates

### Requirement: `revisions` Writes Only Derived State, Never The Bundle

`openkos revisions` MUST write only to `.openkos/findings.db` (the subject
cache and revision findings tables). It MUST NOT modify any file under
`bundle/`. A `REAFFIRMS` or `UNRELATED` verdict MUST be persisted as a
finding but MUST cause no bundle write and MUST NOT be offered for any
relation.

#### Scenario: A full run changes no bundle file

- GIVEN a bundle with Decisions eligible for detection
- WHEN `openkos revisions` runs to completion, accepting both gates
- THEN no file under `bundle/` is created, deleted, or modified, and only
  `.openkos/findings.db` changes

#### Scenario: REAFFIRMS and UNRELATED cause no bundle write

- GIVEN a judged pair whose verdict is `REAFFIRMS` (or, separately,
  `UNRELATED`)
- WHEN the finding is persisted
- THEN no relation is written to either Decision's document

### Requirement: Revision Findings Persist In Sibling Tables

Revision findings and the subject cache MUST be stored in tables that are
siblings of, and separate from, the existing `findings` table in
`.openkos/findings.db`. Every existing reader of the `findings` table
(contradiction serving, `status`, `next`, pending-work surfaces, and
`_partition_persisted_serves`) MUST be unaffected by the presence of
revision-finding or subject-cache rows.

#### Scenario: Contradiction serving is unaffected by revision findings

- GIVEN a bundle with both persisted contradiction findings and persisted
  revision findings for the same pair of concepts
- WHEN `openkos contradictions` runs
- THEN it reports the same result it would report if no revision findings
  existed for that pair

#### Scenario: status/next/pending surfaces are unaffected

- GIVEN a bundle with persisted revision findings
- WHEN `openkos status`, `openkos next`, or a pending-work surface runs
- THEN its output is unaffected by the existence of revision findings or
  subject-cache rows

### Requirement: Revision Findings Are Served From Cache Keyed By Input Digests

Each persisted revision finding MUST be keyed by input digests covering
both Decisions' bodies and their reached Sources. A `revisions` run MUST
serve a finding from the cache, with no judge LLM call, when its input
digests are unchanged since the finding was last computed.

#### Scenario: An unchanged bundle makes zero LLM calls on re-run

- GIVEN a bundle whose Decisions, their bodies, and their reached Sources'
  `event_date` values are unchanged since the last `revisions` run
- WHEN `revisions` runs again
- THEN every previously judged pair is served from persisted findings and
  no LLM call (subject or judge) is made

#### Scenario: Editing a Decision's body re-judges only pairs containing it

- GIVEN a bundle with several persisted revision findings, where one
  Decision's body is edited after the last run
- WHEN `revisions` runs again
- THEN only pairs containing the edited Decision are re-judged; every other
  persisted finding is served unchanged

#### Scenario: Editing a Source's event_date re-judges only affected pairs

- GIVEN a bundle with several persisted revision findings, where one
  Source's `event_date` is edited after the last run
- WHEN `revisions` runs again
- THEN only pairs whose Decisions reach that Source are re-judged; every
  other persisted finding is served unchanged

### Requirement: Revisions Report Groups Findings Per Decision, Including REAFFIRMS

The `revisions` report MUST group findings per Decision. For a Decision
with a persisted `REAFFIRMS` finding, the report MUST include a line
reading "reaffirmed by `<id>` on `<date>`" (or the equivalent for an
undated reaffirming pair), under that Decision's group.

#### Scenario: A REAFFIRMS finding appears under its Decision's group

- GIVEN a persisted `REAFFIRMS` finding between Decisions `alpha` and
  `beta`, where `beta` reaffirms `alpha` on a known date
- WHEN the `revisions` report renders
- THEN `alpha`'s group includes a line reading "reaffirmed by `beta` on
  `<date>`"

#### Scenario: Re-running after serving still renders REAFFIRMS from persisted findings

- GIVEN a persisted `REAFFIRMS` finding with unchanged input digests
- WHEN `revisions` runs again
- THEN the report still includes that finding's line, with zero LLM calls
  made to produce it
