---
type: Decision
title: "ADR-0025: Temporal direction between two concepts never comes from a model"
description: The order between two Decisions is taken only from the event dates their provenance reaches; a judge's reply schema has no field that can express order, and the stored finding keeps the dates, never a holder.
status: Accepted
date: 2026-09-25
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-09-25T00:00:00Z
sensitivity: public
---

# ADR-0025: Temporal direction between two concepts never comes from a model

- **Status:** Accepted
- **Date:** 2026-09-25

## Context

The decision-revision detector (#1014 piece a) needs to know the **order** between two Decisions: which one came later. The later Decision holds the `supersedes` or `revises` edge that `reconcile` writes (ADR-0024), and a `supersedes` edge hides its target from retrieval. A wrong order therefore hides the decision that is still in force and keeps the obsolete one current.

The forces:

- **Models are poor at chronology and confident about it.** A judge asked "which came later" answers from the phrasing ("we now…", "going forward…"), not from facts. Stated confidence carries no correctness signal in this project's measurements (#558, #870). A Source's `event_date` (ADR-0023) is a recorded fact that a human can check and correct.
- **Derived stores are caches.** AGENTS.md requires everything derived to rebuild from the canonical files. `.openkos/findings.db` already holds several tenants of recomputable machine output (contradictions, adjudications, edge suggestions, and now revision findings). Each uses per-row digest staleness, and each takes part in the `forget` sweep and in `purge`'s deletion of the whole file. A revision finding's stored dates follow the same discipline: they are read back from the Sources at judge time, never re-derived by a model, and the table that stores them joins the same erasure paths.

## Decision

**Temporal direction between two concepts is never taken from a model.**

- The direction of a revision comes only from the `event_date` of the Sources each Decision's provenance reaches. The later-dated side holds the edge.
- When either side has no reached Source, a missing or malformed date, more than one distinct date, or when both dates are equal, there is **no direction**. The human is asked which Decision is later before anything is written.
- The judge's reply schema has no field that can express order. The stored finding does not store a holder: it stores the dates and each side's date state, and one pure function (`resolution.decision_revision.pair_direction`) derives direction from them every time it is needed.

## Consequences

Easier:

- A wrong direction can only come from a wrong recorded date. That date is visible, attributable, and fixable with `ingest --event-date`.
- There is no stored holder that could disagree with the dates it was computed from -- one pure function is the only authority on direction.
- Privacy handling reuses the existing `findings.db` erasure paths. That holds as long as each new table joins the sweep in the same change that creates it (#1014 Phase B P3 joins `revision_findings`).

Harder, or accepted:

- Many real Decisions are undated, so many findings will ask the human for the order. This is the price of never guessing.
- Two dated Decisions from the same day cannot be ordered automatically. Equal dates count as no direction.

## Alternatives considered

- **Letting the judge state which Decision is later**, or using its order when the dates are missing. Rejected: it is unverifiable, confidently wrong on phrasing, and it decides what `supersedes` hides.
- **Ingest time, file modification time, or the earliest/latest of several dates** as a fallback order. Rejected. Ingest and file times record when text reached the tool, not when the meeting happened. Min/max over several dates was declined by the human for merged concepts (exploration, open decision 3).
- **Storing the resolved holder on the finding.** Rejected: that is a second authority on direction, and it could disagree with the dates it was computed from.
- **A rebuildable cache of LLM-derived per-concept attributes** (a Decision subject, value, and evidence quote, keyed by concept id, a digest of the exact prompt input, and a prompt version derived from the prompt text itself), stored as a sibling `findings.db` tenant, never in frontmatter. This was this ADR's original scope alongside the direction decision above. **Deferred** (design.md Decision B3, accepted by the owner 2026-09-28): the Phase B re-plan drops the production subject pass entirely -- `plan_revision_candidates` groups candidates by embedding proximity instead of a computed subject, so the cache has no consumer to build it for. The rule returns, under its own ADR, with its first real consumer: frontmatter is canonical and user-owned, so an LLM-derived per-concept attribute must never be written there; it belongs in a digest-keyed cache that a prompt-version bump invalidates for free, exactly as this tenant's revision findings already do for the judge's verdicts.
