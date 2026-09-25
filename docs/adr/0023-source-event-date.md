---
type: Decision
title: "ADR-0023: A Source records its event date as an optional, never-defaulted event_date key"
description: A Source may carry an event_date frontmatter key holding a quoted ISO calendar date, set only from user-controlled evidence and never defaulted to ingest time.
status: Proposed
date: 2026-09-25
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-09-25T00:00:00Z
sensitivity: public
---

# ADR-0023: A Source records its event date as an optional, never-defaulted event_date key

- **Status:** Proposed
- **Date:** 2026-09-25

## Context

A Source's `timestamp` is the time it was ingested. It is never the time the recorded event happened. Re-ingest stamps it again on every run. Issue #1014 needs two later features: revision detection, and superseded history in `query`. Both must order two Sources by when their events happened, and ingest order is not event order. A call recorded in July and ingested in September is older than a note written in August and ingested on the same day.

Nothing in the engine models event time today. The value this decision introduces gets written into users' bundles, and later code will read it. The key name, the value format and the meaning of absence therefore form an on-disk interface. Changing any of them after release requires a migration of every bundle that holds the key.

The forces:

- **Unknown must stay distinct from known.** An ingest-time default cannot be told apart from a real date. It would give the downstream ordering a confident, wrong direction.
- **The evidence is a date and nothing more.** The two inputs the user controls, an explicit flag and a date in the file name, state neither a time of day nor a time zone.
- **YAML implicit typing.** Under YAML 1.1 an unquoted `2026-07-14` is a timestamp. PyYAML loads it as `datetime.date`. A YAML 1.2 core-schema parser loads it as a string, and some JavaScript parsers load it as a UTC-midnight `Date`. An OKF bundle is read by tools other than this engine.
- **Adopt OKF.** OKF v0.1 §4.1 allows extension keys in frontmatter and requires readers to tolerate them. It defines no event-time field of its own.

## Decision

A Source concept document MAY carry the frontmatter key **`event_date`**. It is an OKF §4.1 extension.

1. **Format.** The value is an ISO 8601 calendar date `YYYY-MM-DD` with no time and no zone. It is written as a **quoted YAML string** (`event_date: '2026-07-14'`), so every YAML parser reads the same text. Readers also accept an unquoted date that a person typed by hand. Any other value is malformed: it is ignored, it is never carried forward, and it is reported as a warning. It never fails the run.
2. **Never defaulted.** When no user-controlled evidence exists, the key is absent. It is never filled from the ingest time, the file's modification time, or a model's reading of the text. Absence means "unknown". A Source written before this key existed is byte-identical to a Source ingested without evidence today.
3. **Evidence and precedence.** On first ingest the order is: the explicit `--event-date` flag, then the file name, then unset. On re-ingest the order is: the flag, then the stored value, then the file name, then unset. The file name counts as evidence only when the basename holds exactly one distinct, calendar-valid, digit-bounded `YYYY-MM-DD` token. If any date-shaped token in the name is invalid, or two different dates appear, no date is taken.
4. **Only on Sources.** Derived concepts do not store the key. They reach event time through `provenance`, so the value keeps one canonical home. A merge keeps the survivor's own `event_date` and never takes the absorbed side's value.

## Consequences

- Revision detection and superseded history get a durable input. They can also tell "no evidence" apart from "known". A pair of Sources where either side has no `event_date` has no automatic direction. The later features own that rule.
- Existing bundles do not change until a Source is re-ingested with evidence. There is no migration and no sweep.
- A date in a file name can be a download or export date rather than the event date. This risk is accepted. Ingest names the origin of every value it records (`--event-date`, the file name, or the existing Source), and an explicit flag on re-ingest corrects it.
- The key is part of the OKF surface that `model/okf.py` owns. Renaming it, or widening it to a datetime, needs a new ADR and a reader that still accepts `YYYY-MM-DD`. Widening is compatible because an ISO date is a strict prefix of an ISO datetime.
- Merge reconciliation has one more special-case key. A future scalar key on a Source must decide explicitly whether the generic "adopt when absent" rule applies to it.

## Alternatives considered

- **Default to ingest time.** Rejected. This is the defect #1014(c) exists to remove, and an ingest-time default carries no information.
- **`event_time` holding an RFC 3339 datetime.** Rejected. No input supplies a time or a zone, so storing one would invent evidence. The name would also promise a precision the value never has.
- **`as_of` or `occurred_at`.** `as_of` collides with the body-prose `(as of YYYY-MM-DD)` freshness stamp that the lint reads. `occurred_at` suggests a moment in time, as `event_time` does.
- **An unquoted YAML date.** It is easier to read, but its type depends on the parser, which is a portability hazard for a format read by third-party tools. Readers still tolerate it when a person writes it by hand.
- **Extracting the date from the text with the LLM.** Deferred. A wrong date produced by a model looks authoritative, and neither downstream feature needs it to start.
- **Storing the date on every derived concept.** Rejected. It can be rebuilt from `provenance`, and a copy would need its own drift, merge and re-ingest rules.
