---
type: Decision
title: "ADR-0044: The Quiet Engine arc precedes Interoperability"
description: A named arc that cuts the decisions OpenKOS asks per source is placed between MVP 4 and MVP 5; MVP numbers stay, and the structural auto-merge class stays opt-in under ADR-0034.
status: Proposed
date: 2026-10-02
tags:
  - openkos
  - adr
  - roadmap
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-02T00:00:00Z
sensitivity: public
---

# ADR-0044: The Quiet Engine arc precedes Interoperability

- **Status:** Proposed
- **Date:** 2026-10-02

## Context

The roadmap states that MVP 6 exists so a non-technical user can use OpenKOS without a terminal, and that "the human curates, the engine maintains" currently means `[y/N]` prompts. MVP 6 as scoped changes the surface, not the load: a desktop app built over today's engine would present the same review queue with buttons.

A human end-to-end run of the 0.4.0 release (issues #1258–#1267) measured that load. Twenty small markdown sources produced 157 documents and 104 pending rows; clearing them took about 4.3 prompts per source, and a candidate cap still hid further groups. Most of those decisions were created by the engine itself: a concept name that already existed was written as `<slug>-N` instead of being attached to the existing concept, and editing a watched source re-extracted it into more copies. Declining a prompt recorded a permanent keep-distinct ruling with no way to say "not now". Unattended writes left derived indexes stale, which produced a false sufficiency refusal on a question the corpus answers verbatim.

[ADR-0034](0034-identity-auto-merge-only-for-a-measured-class.md) already settles when an Identity merge may skip prior consent: only an opt-in class that passed a pre-registered measurement, announced, committed, logged and reversible. Its first candidate signal, judge confidence, failed. Structural signals have not been measured.

Placing OKF export and import (MVP 5) first would validate exchange for a product few people can operate. The forces: reduce the per-source decision load before building a surface on top of it, without re-deciding anything ADR-0034 or ADR-0039 settled, and without invalidating accepted ADRs that name later MVPs.

## Decision

We add a named arc, **The Quiet Engine**, between MVP 4 and MVP 5. Its single goal is to cut the number of decisions OpenKOS asks of a person per ingested source and to make the remaining ones optional.

- **Numbering stays.** The arc is named, not numbered. MVP 5 (Interoperability) and MVP 6 (The Desktop App) keep their numbers and content, so accepted ADRs that name them (for example ADR-0039) stay accurate.
- **Position.** The arc is built before MVP 5. Delaying OKF export is accepted as part of this placement.
- **The structural auto-merge class stays opt-in.** If the class passes its pre-registered measurement it ships opt-in and off by default, exactly as ADR-0034 requires. Making it default-on would need a later ADR that supersedes ADR-0034, backed by evidence from use.
- **Attach-at-ingest exclusions.** Event and Person are excluded at first, because identical titles routinely name different things (homonyms), and keep today's behavior until measured.
- **Pre-registration.** The arc's product-metric bars (human decisions per ingested source; time from dropping a file to the first answer that can cite it) are fixed in a pre-registration before the arc starts, and they become its exit criteria.

Each behavioral deliverable of the arc gets its own OpenSpec change. This ADR records only the placement and the constraints above.

## Consequences

- The roadmap gains a section for the arc between MVP 4 and MVP 5; nothing in MVP 5 or MVP 6 changes.
- OKF export, and with it the first third-party test of the conformance claim, arrives later than it otherwise would.
- Every class without a passing measurement keeps per-item consent; irreversible operations keep explicit consent. ADR-0034, ADR-0039 and the "human curates, engine maintains" principle are unchanged.
- The arc can fail its measurement. If the structural class does not pass, nothing ships for it and the measurement is recorded.
- Event and Person keep forking duplicates at ingest until a later measurement admits them, so their review load is not reduced by this arc.

## Alternatives considered

- **Insert the arc as MVP 5 and renumber.** Interoperability would become MVP 6 and the desktop app MVP 7. Rejected: it would force edits across accepted ADRs and docs that name the current numbers, for no gain over naming the arc.
- **Make the structural class default-on by superseding ADR-0034 now.** The usability goal argues for it. Rejected: ADR-0034 chose opt-in deliberately, no structural signal has been measured yet, and a default should follow evidence from use, not precede it.
- **Include Person in attach-at-ingest from the start.** Rejected: person-name homonyms are a known conflation risk, and attaching by name would silently merge different people. It stays out until measured.
- **Build MVP 5 first.** Rejected: it validates exchange for a product few people can operate, and the desktop app that follows would wrap today's review load.
