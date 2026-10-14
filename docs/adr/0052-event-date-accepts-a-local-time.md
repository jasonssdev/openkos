---
type: Decision
title: "ADR-0052: A Source's event_date may carry a naive local time, and direction compares at the finest precision both sides carry"
description: The event_date key widens from a calendar date to a calendar date or a naive local date-time, so two Decisions made on one day can be directed; an older engine reads a date-time as malformed.
status: Accepted
date: 2026-10-13
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-13T00:00:00Z
sensitivity: public
---

# ADR-0052: A Source's event_date may carry a naive local time, and direction compares at the finest precision both sides carry

- **Status:** Accepted
- **Date:** 2026-10-13

## Context

ADR-0023 fixed `event_date` as a calendar date, and ADR-0025 derives the direction of a revision from the two dates alone. When both sides resolve to the same date, the direction is `equal` and unknown. That is correct for a date, and it is a real gap for meeting series that run on one day: workshops, sprint days, design sessions. In the AMI Meeting Corpus the four meetings of a scenario can share one date, and one series runs 11:03, 12:03, 14:13 and 15:34 on a single day. Every REVERSES or REFINES finding between such meetings becomes an undirected change that a person must order by hand. The only workaround today is to invent consecutive dates, which writes false evidence into the bundle (#1349).

The forces:

- **Direction is a recorded fact, never a guess.** ADR-0025 stands. The new input must still be user-supplied evidence about the Source.
- **The key is an on-disk interface.** ADR-0023 observed that widening it to a datetime is compatible for a new reader, because an ISO date is a strict prefix of an ISO date-time. The cost falls on an older engine.
- **Zones are out of scope.** No input the user controls supplies a zone reliably. Comparing a zoned value with a naive one raises in Python and is meaningless in a bundle.
- **Mixed precision is the normal case.** A bundle holds old date-only Sources next to new date-time ones.

## Decision

1. **Widen the existing key.** `event_date` accepts `YYYY-MM-DD` (unchanged) or a naive `YYYY-MM-DDTHH:MM` or `YYYY-MM-DDTHH:MM:SS`. This amends ADR-0023 decision 1 on the value format. Every other part of ADR-0023 stands: quoted string on write, never defaulted, same evidence precedence, Sources only.
2. **Naive local time.** The value is the wall-clock time at the place the event happened, with no offset. An offset or `Z`, fractional seconds, a space separator, and week or ordinal dates are refused: the CLI exits `2`, and a stored value is malformed exactly as today. The values are meant to be compared with each other, so they are only meaningful within one zone. That is a documented limit, not something the engine checks.
3. **Canonical text.** A date-time is written as `YYYY-MM-DDTHH:MM`, and with `:SS` only when the seconds are non-zero. One instant has one text, so a re-ingest that restates `T10:00:00` does not rewrite the Source. A date-only value is written byte-identically to today.
4. **Finest common precision.** To direct a pair, the engine compares at the finest precision both sides carry. If both carry a time, it compares date-times, and equal date-times are `equal`. If either side is date-only, it compares dates, and the same day is `equal` as today. It never invents a time for a date-only side.
5. **One Source, several values.** The distinct-value test that yields `multiple` runs on the parsed values. A date and a date-time on the same day are two distinct values, so they are `multiple`: the engine does not claim they describe one event.

## Consequences

- Same-day series can be directed with true evidence, and no fake dates are needed.
- Date-only bundles are byte-identical, and nothing migrates.
- **Accepted tradeoff:** an older engine reads a date-time value as malformed. It treats that Source as undated, warns, and never carries the value forward on re-ingest. Direction involving that Source becomes unknown on the older engine, never wrong.
- Findings stored before this change keep their date-only text and stay readable. The findings store holds the canonical text of whichever value was resolved.
- Every place that renders or compares a resolved event date must go through the shared parse and format helpers, because a `datetime` is a `date` subclass and ordinary comparisons between the two raise.
- Time zones are not modelled. A bundle that mixes zones gets wrong order within a day, and there is no check for it.

## Alternatives considered

- **A separate `event_time` key next to an unchanged `event_date`.** It degrades better: an older engine keeps the date and only loses the tie-break. It was weighed and not chosen. It adds a second key whose meaning depends on the first, with its own absence, merge, carry-forward and malformed rules, and the issue itself called widening the simpler form. The owner chose to widen (2026-10-13) and accepted the older-engine cost.
- **An ordinal `event_sequence`.** It also orders sources that have no clock time. It is a different concept, an order within a series, and it needs a series identity to mean anything. It stays a possible follow-up for sources that have order but no time.
- **Require an offset (RFC 3339).** Rejected. No input supplies a zone, so requiring one invents evidence, which ADR-0023 refuses.
- **Compare date-only sides as midnight.** Rejected. It would direct a date-only Source against a date-time one on the same day, from a time nobody stated.
