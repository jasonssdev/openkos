---
type: Decision
title: "ADR-0025: LLM-derived concept attributes live in a rebuildable cache, and temporal direction never comes from the model"
description: A per-concept attribute computed by an unmeasured LLM prompt is stored in a digest-keyed cache in .openkos/findings.db, never in frontmatter; the order between two concepts is taken only from recorded Source event dates.
status: Proposed
date: 2026-09-25
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-09-25T00:00:00Z
sensitivity: public
---

# ADR-0025: LLM-derived concept attributes live in a rebuildable cache, and temporal direction never comes from the model

- **Status:** Proposed
- **Date:** 2026-09-25

## Context

The decision-revision detector (#1014 piece a) needs two things that do not exist on a Decision today.

The first is a **subject** and a **value**: what was decided about, and what was chosen. It also needs an evidence quote for each. An LLM pass derives them from the Decision's body. The prompt is new and unmeasured. Sub-change 3's harness is expected to change it, possibly more than once.

The second is the **order** between two Decisions: which one came later. The later Decision holds the `supersedes` or `revises` edge that `reconcile` writes (ADR-0024), and a `supersedes` edge hides its target from retrieval. A wrong order therefore hides the decision that is still in force and keeps the obsolete one current.

The forces:

- **Frontmatter is canonical and user-owned.** It is also an on-disk interface that every OKF consumer reads. Writing LLM output there would silently rewrite users' documents. It would leave stale values behind whenever the prompt changes, and a reader would take it for source of truth. Every value OpenKOS has written to frontmatter so far is either human-decided or deterministic.
- **Derived stores are caches.** AGENTS.md requires everything derived to rebuild from the canonical files. `.openkos/findings.db` already holds three tenants of recomputable machine output (contradictions, adjudications and edge suggestions). Each uses per-row digest staleness, and each takes part in the `forget` sweep and in `purge`'s deletion of the whole file.
- **Models are poor at chronology and confident about it.** A judge asked "which came later" answers from the phrasing ("we now…", "going forward…"), not from facts. Stated confidence carries no correctness signal in this project's measurements (#558, #870). A Source's `event_date` (ADR-0023) is a recorded fact that a human can check and correct.
- **The pattern will be copied.** This is the first per-concept LLM attribute. The next ones will be a speaker, a status, or a subject for other types. Whatever this change does becomes the default.

## Decision

**LLM-derived per-concept attributes live in a rebuildable cache, never in frontmatter.** The Decision subject, value and evidence are stored in `.openkos/findings.db` as a new tenant table. Each row is keyed by concept id, a digest of the exact prompt input, and a prompt version derived from the prompt text itself, so editing the prompt invalidates the cache without a manual bump.

The cache is filled lazily by the verb that needs it. It is pruned when a concept disappears. It is erased for a purge set by `forget` and wholesale by `purge`. Deleting it costs only LLM time. `model/okf.py` gains no key for these values.

**Temporal direction between two concepts is never taken from a model.**

- The direction of a revision comes only from the `event_date` of the Sources each Decision's provenance reaches. The later-dated side holds the edge.
- When either side has no reached Source, a missing or malformed date, more than one distinct date, or when both dates are equal, there is **no direction**. The human is asked which Decision is later before anything is written.
- The judge's reply schema has no field that can express order. The stored finding does not store a holder: it stores the dates, and one pure function derives direction from them every time it is needed.

An attribute may be promoted from the cache to frontmatter only by a later ADR, after a harness has measured it, and only as an extension that follows OKF §4.1.

## Consequences

Easier:

- Prompt iteration in sub-change 3 is free of migrations. A new prompt version simply misses the cache, and nobody's documents change.
- Users' canonical files stay free of unreviewed machine output, and the bundle stays byte-stable across detector runs.
- A wrong direction can only come from a wrong recorded date. That date is visible, attributable, and fixable with `ingest --event-date`.
- Privacy handling reuses the existing `findings.db` erasure paths. That holds as long as each new table joins the sweep in the same change that creates it.

Harder, or accepted:

- Subjects exist only on the machine that computed them. They are not portable with the bundle, and other OKF tools cannot see them. A user who moves the bundle pays the subject pass again.
- Many real Decisions are undated, so many findings will ask the human for the order. This is the price of never guessing.
- Two dated Decisions from the same day cannot be ordered automatically. Equal dates count as no direction.
- Every future per-concept LLM attribute inherits this rule. Putting one in frontmatter needs its own ADR and measurement.
- Each new cache tenant must join the `forget` sweep over **every** column that names a concept, not just its primary id. A tenant that is missed leaks quoted text past a `forget`.

## Alternatives considered

- **A frontmatter extension read and written through `model/okf.py`**, following the tolerant `read_event_date` pattern. Rejected: it rewrites canonical user documents with unmeasured output, goes stale on every prompt change, and reads as truth.
- **Computing subjects inside the shared extraction prompt at ingest.** Rejected by the human (exploration, open decision 1). It has the largest blast radius and would need an A/B measurement first.
- **Letting the judge state which Decision is later**, or using its order when the dates are missing. Rejected: it is unverifiable, confidently wrong on phrasing, and it decides what `supersedes` hides.
- **Ingest time, file modification time, or the earliest/latest of several dates** as a fallback order. Rejected. Ingest and file times record when text reached the tool, not when the meeting happened. Min/max over several dates was declined by the human for merged concepts (exploration, open decision 3).
- **Storing the resolved holder on the finding.** Rejected: that is a second authority on direction, and it could disagree with the dates it was computed from.
