# Proposal: keep-meeting-event-beside-subjects

Refs #1689 (the other case of #1642). Governing decision: ADR-0085 (Proposed).

## Intent

On a meeting-shaped source, `_drop_framing_objects` removes every non-`Procedure`
object whose own title is meeting-shaped, so the meeting never survives as an
object, even when the reply also carries the subjects it discussed. That was right
for the defect it was built against (#522, #533: the meeting replacing its
subjects and winning the retained prefix). It also removes a legitimate
representation: the meeting as one bounded `Event` beside its subjects, carrying
the participants and linking to what it produced.

The pre-registered measurements licensed it: `evals/meeting_event/RESULTS.md`
(ADVANCE-WITH-CAP-EXEMPTION), `evals/meeting_event_roster/RESULTS_RETRY.md`
(LICENSED on both arms), `evals/meeting_event_roster/PREREGISTRATION_NAMED.md`
(named-speaker roster rule). Their closing rules say nothing is adopted by a
results file; this change is the production change they call for.

Success: on a meeting-shaped source where at least one subject survives, the
result carries exactly one more object, a meeting `Event` whose description and
body were composed by one bounded call from the source's own speaker roster; the
subjects, their order and the cap are untouched.

## Scope

### In Scope

1. After the framing drop, twin rule and re-ask, one meeting `Event` candidate
   (an `Event` the framing drop removed that the language gate and twin rule
   would have kept; highest title grounding, ties to the earlier position) is
   composed and appended OUTSIDE the cap, only when a subject survives. Both
   extraction paths (`extract_concept`, `extract_concept_union`).
2. The compose call: the roster-grounded prompt `extraction/meeting_event`
   (hash `4f93593ea5bea022`, byte-identical to the measured
   `EVENT_ROSTER_SYSTEM_PROMPT`), the `SPEAKERS IN THE SOURCE (complete list)` line
   built by `speaker_roster`, which shares one definition of a speaker with
   `_transcript_shaped_text`; one re-ask on an unparseable reply; a body that
   names a participant the source lacks, or that is out of bounds, is rejected
   and no Event is stored.
3. `max_extraction_calls` counts the compose call and its re-ask.
4. `ExtractionReport.meeting_event_added_titles`; a failed compose appears in
   `optional_call_failures`, a bounded prompt in `bounded_prompt_calls`.
5. A delta to `extraction-union-judge`, ADR-0085, the CHANGELOG.

### Out of Scope

- The empty-reply case (#1642): a re-ask, not this change.
- `_MEETING_SHAPED_TITLE_RE`, the language gate, the extraction prompt, the
  judge, the ceiling, the backstop, `ATTACH_EXCLUDED_TYPES`,
  `RECURRING_OCCURRENCE_TYPES`: unchanged.
- Re-ingest reconciliation of the same meeting beyond what existing Event
  identity already does.
- A configuration key to turn it off (see Rollback).

## Approach

See ADR-0085 and the design notes in `tasks.md`. A new leaf module
`extraction/meeting_event.py` holds the pure parts (candidate choice, prompt
messages, reply parsing, participant check, the compose call);
`extraction/concept.py` gains `speaker_roster` and the wiring.

## Rollback

Revert the change. There is no stored state to migrate: the Event is an ordinary
object, and an existing bundle is unaffected. A `meeting_event` key was not added
because no measurement priced a default-off arm; adding one is a small follow-up.
