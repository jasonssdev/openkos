---
type: Decision
title: "ADR-0072: Contradictions and revisions are previewed as findings and applied as decisions"
description: openkos.api settles contradiction and revision rows with the reconcile and decline writes the CLI uses, previewed by a second function and route (preview_finding) whose plan union is its own, and applied by the existing apply_decision against the same DecisionVersion; watch refusals stay without an action until ingest reaches the API.
status: Accepted
date: 2026-10-17
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-17T00:00:00Z
sensitivity: public
---

# ADR-0072: Contradictions and revisions are previewed as findings and applied as decisions

- **Status:** Accepted
- **Date:** 2026-10-17

## Context

To review lists contradiction, revision and refused-file rows that the app
cannot settle (#1476), although the CLI settles the first two without a model:
`contradictions --decline` records a decline, and `reconcile` records a
`reconciled_with`, `supersedes` or `revises` edge, in its two-id form and in the
`--from-findings` walk over both kinds. A refused file resolves through
`ingest`, which runs extraction with a model.

The decision surface is two-phase ([ADR-0053](0053-curation-decisions-are-a-two-phase-application-surface.md)):
a preview returns a plan and a `DecisionVersion`, and `apply_decision` commits
against it. `openkos.api` is versioned
([ADR-0060](0060-the-stable-python-api-is-a-versioned-facade-with-one-error-root.md)),
and its classifier treats a widened non-`Literal` union as breaking. A reconcile
or a decline fits none of the four plan types of `DecisionPreview.plan`, so
adding them there would mean `API_VERSION` 2, for every client, for one new kind
of plan. Over HTTP ([ADR-0061](0061-the-local-api-is-a-fastapi-adapter-over-the-python-api-shipped-as-an-optional-extra.md)),
the review screen switches over that union exhaustively; widening it would break
the screen's build under a change that does not touch the screen.

## Decision

1. **A second preview, one apply.** `preview_finding(workspace, decision_key,
   action)` returns a `FindingPreview` whose plan is `ReconcilePlan |
   DeclinePlan`, with the same `DecisionVersion`. `apply_decision` applies it, so
   the apply contract (lock only inside the commit section, a commit of only the
   paths written, outcomes as values) is one contract. Each preview refuses the
   other's actions with `NotApplied("wrong_preview")`. Over HTTP the route is
   `POST /v1/decisions/{key}/preview-finding`.
2. **The CLI's writes, not new ones.** A decline is
   `apply_contradiction_decision`; a reconcile, supersede or revise is the
   `reconcile` transaction, split into a public prepare and commit so the token
   path can re-check the row inside the section. The actions per kind are the
   ones the CLI's walks offer, directed relative to the decision's targets, with
   a revision's direction taken from its dates, never from the model
   ([ADR-0025](0025-temporal-direction-never-comes-from-the-model.md)).
3. **New detail on a new field.** `Decision.finding`, defaulted to `None`, carries
   the contradiction, revision or refused-file detail; `Decision.detail` keeps its
   union.
4. **No action where the remedy calls a model.** A refused file is listed with
   why it was refused and no action until an ingest operation, quoted and
   consented like judging, reaches the API (#1496).

## Consequences

- The app can settle contradictions and revisions with no terminal, with the
  same writes and queue resolutions as the CLI, inside v1.
- A client calls one of two previews, picked by the decision's kind; the wrong
  one says so instead of failing silently. That is one more name to learn than a
  single preview, and a third plan family later (an ingest, say) would follow the
  same shape or wait for v2.
- When v2 comes, the two unions can merge into one preview; until then
  `Decision` has two detail fields, one per family.
- A reconcile now resolves the revision row over its pair, from the CLI too, so
  an applied revision leaves To review at once.
- Refused files still need the terminal or the person's folder to settle.

## Alternatives considered

- **Widen `DecisionPreview.plan` and bump `API_VERSION` to 2.** One preview, but a
  breaking release for every client to add two plan types, and the review
  screen's exhaustive switch breaks in a change that does not touch it. Rejected.
- **Carry a reconcile in `RelatePlan` and a decline in `KeepDistinctPlan`.** No
  version bump, but a client reading `kind: "relate"` would word a supersede as a
  connection and a decline as "keep separate". Rejected: a plan has to say what
  it does.
- **A separate apply per family.** Nothing in the apply differs, and a second
  apply would be a second contract to keep equal. Rejected.
- **Expose ingest now for refused files.** It needs a quote, a consent, a job and
  an answer for the "changed after import" case, where a hand ingest refuses too.
  Deferred to its own change.
