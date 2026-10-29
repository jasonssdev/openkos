# Delta for extraction-union-judge

## ADDED Requirements

### Requirement: A Meeting Is Kept As An Event Beside Its Subjects

On a source the framing drop applies to, WHEN at least one subject survives the
framing drop, the source-title twin rule and the re-ask, extraction MUST add
exactly one meeting `Event` to its result, composed by one bounded call, on both
extraction paths. The candidate MUST be an `Event` that `_drop_framing_objects`
removed and that the wrong-language gate and the twin rule would have kept; of
several, the one with the highest title grounding (the share of the title's words
of three or more letters that occur in the source) MUST win, a tie going to the
earlier reply position. Other types MUST NOT be candidates. The Event MUST be
placed after every retained subject and MUST NOT count against
`_MAX_OBJECTS_PER_SOURCE` or the union backstop; the retained subjects, their
order and the cap MUST be exactly what they were without it. WHEN no subject
survives, or no candidate exists, nothing MUST be added.

#### Scenario: The Event is added beside its subjects

- GIVEN a meeting-shaped source whose reply carries three subjects and an
  `Event` titled after the meeting
- WHEN extraction runs
- THEN the result holds the three subjects in their order followed by one
  `Event` with the meeting's title

#### Scenario: The Event does not take a subject's place under the cap

- GIVEN the same source with eight subjects, so the cap binds
- WHEN extraction runs
- THEN the first six subjects are retained as before and the Event follows them

#### Scenario: No subject, no Event

- GIVEN a reply whose only object is the meeting `Event`
- WHEN extraction runs
- THEN the result is empty and no compose call is made

#### Scenario: Only an Event is a candidate

- GIVEN a reply with a subject and a `Project` titled after the meeting
- WHEN extraction runs
- THEN the `Project` stays dropped and no Event is added

#### Scenario: A source the framing drop does not apply to is untouched

- GIVEN a source that is not meeting-shaped
- WHEN extraction runs
- THEN no compose call is made and no Event is added

### Requirement: The Meeting Event Is Composed From The Source's Speaker Roster

The compose call MUST use the prompt `extraction/meeting_event`, whose content
hash MUST be `4f93593ea5bea022`, and a user turn carrying the language anchor, the
meeting title, the line `SPEAKERS IN THE SOURCE (complete list): <roster>`
(`none labelled` when the roster is empty), the retained subject titles, and the
source text bounded to the context window like the other whole-source calls. The
roster MUST be `speaker_roster(source_text)`: the labels, in order of first
appearance, of the union of two or more capitals-or-digits labels opening at
least two lines (the AMI form, only when `_transcript_shaped_text` holds for the source) and the labels the meeting detector
(`_transcript_shaped_text`) counts as recurring speakers, which MUST be computed
by the same line and label rules, not by a second definition. The Event's title
MUST be the candidate's; its description and body MUST be the reply's.

#### Scenario: The roster reaches the prompt

- GIVEN a transcript with speakers `Ana` and `Luis`
- WHEN the compose call is made
- THEN the user turn contains `SPEAKERS IN THE SOURCE (complete list): Ana, Luis`

#### Scenario: The roster and the meeting gate agree on a speaker

- GIVEN any text
- WHEN `speaker_roster` and `_transcript_shaped_text` read it
- THEN a label counted as a recurring speaker by one is counted by the other

#### Scenario: An AMI label is read

- GIVEN a source whose lines open `PM:` and `ID:` twice each
- WHEN `speaker_roster` reads it
- THEN it returns `PM` and `ID` in order of first appearance

### Requirement: A Meeting Event Body Is Admitted Fail-Closed

A reply MUST be read with `extract_json_object`; it is usable only when it has a
non-empty string `body`. WHEN the first reply is not usable, the same request MUST
be sent exactly once more, and a second unusable reply MUST end the attempt; a
call that raises MUST NOT be retried. A usable body MUST be rejected, and no Event
stored, WHEN it is shorter than 80 or longer than 1500 characters after
stripping, or WHEN it lists a participant (the enumeration after a participation
verb) that is not a whole-word, case-insensitive substring of the source. A
rejection or a failure MUST NOT remove or change any other object and MUST NOT
raise; a failure MUST be named in `optional_call_failures` by the call name and
the exception type only.

#### Scenario: One re-ask recovers an unparseable reply

- GIVEN a first reply with no closing brace and a valid second reply
- WHEN the compose call runs
- THEN the Event is stored from the second reply and two calls were made

#### Scenario: A second unusable reply ends the attempt

- GIVEN two unparseable replies
- WHEN the compose call runs
- THEN no Event is stored, exactly two calls were made, and the subjects are unchanged

#### Scenario: A body naming an absent participant is rejected

- GIVEN a body that says "participated: PM, the team"
- WHEN the participant check runs against a source with no "the team"
- THEN no Event is stored

#### Scenario: A failing call degrades

- GIVEN the backend raises during the compose call
- WHEN extraction runs
- THEN the subjects are kept, no Event is added, extraction does not raise, and
  `optional_call_failures` holds `meeting_event: <ExceptionType>`

## MODIFIED Requirements

### Requirement: Extraction Exposes An Upper Bound On Its Calls

`extraction.concept` MUST expose `max_extraction_calls(source_text, *,
union_judge, transcript_decisions, selector_judge=False)`, a pure function that returns a number of
chat calls no extraction of `source_text` can exceed, whatever the model
replies and whatever title the source is given. It MUST count each mechanism at
its maximum: two calls per window on a chunked source (the window's retry), two
below the threshold (the union path's two passes, or the single-run path's
retry of an empty reply), one re-ask, two for the meeting Event (its compose call
and its one re-ask) on either path, and on the union path one participant
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
- THEN it is two passes, one re-ask, two meeting Event calls, one participant
  pass, two decision calls and every judge attempt

#### Scenario: The selector judge off removes its attempts from the bound

- GIVEN the same source with `selector_judge` off
- WHEN the bound is computed
- THEN it is smaller by exactly `JUDGE_ATTEMPTS`

#### Scenario: Adversarial replies stay within the bound

- GIVEN a chunked source and replies that fail each window's first attempt and
  never give the judge or the decision pass a usable reply
- WHEN `extract_concept_union` runs
- THEN the calls made are at most the bound and at least two per window
