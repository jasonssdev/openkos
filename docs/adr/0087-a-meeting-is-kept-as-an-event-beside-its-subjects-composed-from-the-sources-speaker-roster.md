---
type: Decision
title: "ADR-0087: A meeting is kept as an Event beside its subjects, composed from the source's speaker roster"
description: Proposes that extraction keep one meeting Event after the subjects, outside the cap, with a description and body composed by one bounded call from the source's own speaker list, because the pre-registered measurements licensed the placement and the roster-grounded body. It prices the extra call and names what the evidence cannot show.
status: Proposed
date: 2026-10-29
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-29T00:00:00Z
sensitivity: public
---

# ADR-0087: A meeting is kept as an Event beside its subjects, composed from the source's speaker roster

- **Status:** Proposed
- **Date:** 2026-10-29

## Context

On a meeting-shaped source, `_drop_framing_objects` removes every non-`Procedure`
object whose own title is meeting-shaped, with no floor and no exception. It was
built against a measured defect: the meeting object replaced its subjects (#522,
27 of 49 stored collapses were exactly the meeting's title) and, sitting at
position 1 in 49 of 59 runs, won the retained prefix at every cap value (#533).

It also removes a representation that is legitimate on its own: the meeting as a
bounded `Event` beside its subjects, answering "what was decided in that
meeting?". Issue #1689 asked whether to keep it. Under the epic's rules
(pre-register, never adopt in a results PR) three measurements were run, each
closing with "production needs an OpenSpec change":

- **Placement** (`evals/meeting_event/RESULTS.md`, model-free replay of 75 Spanish
  and 254 English records): ADVANCE-WITH-CAP-EXEMPTION. No displacement and no
  cardinality violation in 329 cases; 291 of 298 kept objects were meeting
  containers (0.977); appending after the subjects would lose the Event to the
  cap in 153 of 231 English qualifying cases (0.662, bar 0.25), so the Event goes
  outside the cap.
- **Body** (`evals/meeting_event_body/RESULTS.md`): composing a body from the
  source alone was REJECTED, faithful 5 of 28. The unfaithful bodies named
  participants the source does not state ("other members of the team", role
  titles).
- **Roster-grounded body** (`evals/meeting_event_roster/RESULTS_RETRY.md`): with
  the source's speaker list handed over as complete, one re-ask of an unparseable
  reply, and a model-free participant check, both arms LICENSED: 30 of 30 bodies
  restated nothing, linked their subjects, invented no date and named no absent
  participant; faithful 29 of 30 against 0.90 on a second reading made against
  the source. The named-speaker rule (`PREREGISTRATION_NAMED.md`, `RESULTS_NAMED.md`:
  LICENSED, N1 to N6 all hold) generalized the
  roster beyond the AMI label form by reusing the meeting detector's own
  definition of a speaker.

## Decision

On a source the framing drop applies to, when at least one subject survives the
framing drop, the twin rule and the re-ask, we keep one meeting `Event` after the
retained subjects, outside `_MAX_OBJECTS_PER_SOURCE` and the union backstop. The
candidate is an `Event` the framing drop removed that the language gate and twin
rule would have kept, chosen by title grounding. One bounded call composes its
description and body with the frozen prompt `extraction/meeting_event` (hash
`4f93593ea5bea022`, byte-identical to the measured prompt), the user turn
carrying `SPEAKERS IN THE SOURCE (complete list)` built by `speaker_roster`. An
unparseable reply is asked once more. A body that lists a participant the source
lacks, or that is outside 80 to 1500 characters, is refused and no Event is
stored; nothing else about the result changes. The call is optional and fails
closed: its failure is reported and never raised.

We add no configuration key.

## Consequences

Easier: the meeting is a dated, participant-bearing object in the graph that
links, in prose, to what it produced; nothing else in the pipeline moves, and the
subjects, their order and both caps are exactly what they were.

Harder: a meeting-shaped source that qualifies pays up to two more calls (the
upper bound grows by two on every path); the Event body is generated text, so a
reader should treat it as a summary, not as the source. `Event` stays in
`ATTACH_EXCLUDED_TYPES` and `RECURRING_OCCURRENCE_TYPES`, so a re-ingest of the
same meeting is reconciled by existing Event identity rules, which this change
does not measure.

Not shown by the evidence, and accepted knowingly:

- **One model, one prompt, one family.** The body measurements are `qwen3:8b` on
  five machine-translated AMI Spanish meetings; English bodies were measured
  through placement only. The named-speaker rule was measured on synthetic
  fixtures, one committed fictional transcript and the AMI sources, with no real
  named-speaker corpus.
- **Faithfulness rests on a delegated reader**, one per reading, the second made
  after a first verdict was seen (disclosed in `RESULTS_RETRY.md`).
- **Summarized notes** (a gathering word in a heading, no speaker turns) pass the
  same gate and get `none labelled`, so their prompt asks for no participants.
  That is the prompt's stated behavior, not a measured one.
- **The length bound** (80 to 1500) is the registered `bounded` bar applied as an
  admission rule; it never fired in the measurements.
- **The re-ask trigger** (an unparseable reply) has production precedent in its
  mechanism only.

## Alternatives considered

- **Append the Event after the subjects, inside the cap.** Rejected by B4: it
  loses the Event in about two thirds of English qualifying cases.
- **Compose the body from the source and titles alone.** Rejected: faithful 5 of 28.
- **Keep the model's own Event unchanged** (its description and body). Not
  measured; the natural bodies were REJECTED.
- **Repair a truncated JSON reply.** Rejected: extraction is fail-closed
  (`llm/parsing.py`); one re-ask is the measured remedy.
- **A configuration key to turn it off.** Not added: no measurement priced a
  default-off arm, and a key can follow cheaply if the extra call proves
  unwelcome.
- **Keep the meeting when no subject survives.** Rejected: the empty-reply case is
  a model collapse a kept container does not fix (#1642).
