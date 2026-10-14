# Delta for Extraction Union-Judge

## ADDED Requirements

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
