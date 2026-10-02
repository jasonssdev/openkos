---
type: Decision
title: "ADR-0045: Ingest attaches to an existing same-type, same-key concept instead of forking it"
description: When an extracted candidate has the same OKF type and the same normalized title key as an existing, non-deprecated concept, ingest revises that concept (provenance appended, evidence appended deterministically, version bumped, Concept ID kept) instead of writing a numeric-suffixed copy; Event and Person are excluded, and attach_at_ingest turns it off.
status: Proposed
date: 2026-10-02
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-02T00:00:00Z
sensitivity: public
---

# ADR-0045: Ingest attaches to an existing same-type, same-key concept instead of forking it

- **Status:** Proposed
- **Date:** 2026-10-02
- **Issues:** [#1268](https://github.com/jasonssdev/openkos/issues/1268), [#1259](https://github.com/jasonssdev/openkos/issues/1259)

## Context

Ingest decided a collision by slug: a candidate whose slug was already taken
by a concept of a different source was written to `<slug>-2`, `-3`, and so on,
and the pair was later queued as an Identity decision for a person. The 0.4.0
human E2E produced nine such families, and every one was a true duplicate; one
source edited once produced four more copies (#1259). The engine was creating
most of the decisions it then asked a person to clear.

"Immutable sources, living objects" (AGENTS.md) says concept documents are
rewritten over time. The one place the engine instead forked was ingest. The
Concept ID is the identity under OKF (the path minus `.md`), so a suffix added
at ingest time becomes permanent unless a later merge repairs it (#1228).

ADR-0034 governs when an Identity merge between two existing concepts may skip
prior consent: an opt-in class that passed a pre-registered measurement. This
decision is different in kind: it does not merge two existing concepts, it
prevents the second one from being written. It still has to answer
"Representation, not truth": the same type and the same key can name two
things, which is why Event (recurring series) and Person (homonyms) are
excluded (#776, #796).

## Decision

**One. Match structurally.** A candidate attaches to an existing concept when
their OKF `type` is equal and `normalize_key(title)` is equal, the key the
exact-title Identity tier already uses. No model, judge confidence, embedding
or alias takes part (ADR-0034: judge confidence is not a signal). A deprecated
concept is never a target. Of several matches (a pre-existing `base`/`-N`
family) the canonical member is the un-suffixed id, else the lowest `-N`
(#1228).

**Two. Revise, do not replace.** The existing concept keeps its Concept ID,
`type`, `title` and `description`. Its `provenance` gains the Source (and
`sources` is re-projected), `sensitivity` is recomputed as the high-water mark
and never lowers, `tags` are unioned, `freshness` and `generated` come from the
newer side, and `version` becomes the existing integer plus one (an absent or
non-integer value counts as 1; no engine path bumped `version` before). The
candidate's body is appended under `## Update from <Source title>
(sources/<slug>)`, above `## Related`, with its headings demoted; a body
already contained in the existing one adds no section. Nothing already written
is rewritten, and no model is called, so the commit phase (ADR-0036) stays
free of model calls.

**Three. Same-source stays a no-op.** A concept that already lists this Source
in `provenance` is left byte-untouched, so a re-ingest never attaches twice
and never bumps `version` twice.

**Four. Event and Person are excluded** from attach (a named constant, not
configuration) and keep the slug-collision path until a measurement admits
them. Cross-type attach does not exist.

**Five. It is on by default on every ingest path, the unattended watch
included, and `attach_at_ingest: false` is the kill switch.** Every attach is
disclosed (an `**Attach**` line in `log.md`, a count in the ingest outcome, a
line in the run summary) and committed with its ingest, so it is undone by
reverting that one commit.

**Six. The write is guarded.** An attach target is a drift-guarded target read
after extraction returns and rewritten atomically, not a create-only path.

## Consequences

Easier: the `-N` families stop being created for every type except Event and
Person; an edited watched source revises the concepts it produced instead of
multiplying them; review load falls at the source rather than being queued.
The `Concept ID` a person or an external reference learns is the one that
persists.

Harder: an attach rewrites an existing concept unattended, so a wrong match (a
same-key homonym of a non-excluded type) silently stacks one subject's
evidence under another's. The exclusion list, the disclosure and the
one-commit undo bound that, and the arc's pre-registered metrics measure it;
they do not remove it. Concepts grow as stacked sections until `curate`'s
reconcile consolidates them. Two concepts that already form a `-N` family are
not merged by this decision (that is a separate, measured class under
ADR-0034). A concept edited while the model is extracting makes the whole
ingest refuse on drift rather than be overwritten, which a busy watch retries.

## Alternatives considered

- **Keep forking and merge afterwards.** This is the current behavior plus a
  post-hoc structural merge (#1268 deliverable 4). It leaves the permanent
  suffixed id for a window and still creates the work before removing it, and
  its measurement is a prerequisite this decision does not need.
- **Let the model judge SAME at ingest.** One more model call per collision
  and a verdict ADR-0034's measured FAIL says is not reliable enough to act
  without a person.
- **Opt-in, off by default.** The most conservative, and the default behavior
  would stay the fork the arc exists to remove. The owner chose default-on with
  a kill switch.
- **Replace the existing body with the new one.** Loses evidence the earlier
  Source supported, which "provenance is first-class" forbids.
- **Reconcile bodies with a model at attach time.** Reads better but spends a
  model call per attach, rewrites prose unattended, and cannot run in the
  commit phase; `curate` already offers that consolidation afterwards.
