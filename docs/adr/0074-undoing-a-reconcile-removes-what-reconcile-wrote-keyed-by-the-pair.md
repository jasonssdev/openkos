---
type: Decision
title: "ADR-0074: Undoing a reconcile removes what reconcile wrote, keyed by the pair"
description: openkos.api undoes a reconcile with a preview and an apply keyed by the sorted pair, removing the resolution edges as unrelate removes an edge, withdrawing a supersede's out-of-date mark as unrelate withdraws it, and removing the reconciliation notes reconcile appended when they keep the shape it wrote; a declined contradiction is reopened the same way, keyed by the pair and the merged-body discriminator.
status: Proposed
date: 2026-10-17
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-17T00:00:00Z
sensitivity: public
---

# ADR-0074: Undoing a reconcile removes what reconcile wrote, keyed by the pair

- **Status:** Proposed
- **Date:** 2026-10-17

## Context

[ADR-0072](0072-contradictions-and-revisions-are-previewed-as-findings-and-applied-as-decisions.md)
lets the app settle a contradiction or a revision: decline it, or reconcile the
pair as both right (`reconciled_with`), one replacing the other (`supersedes`)
or one updating the other (`revises`). The wording brief gives those
confirmations no Undo (#1497).
[ADR-0067](0067-undoing-a-review-choice-is-a-two-phase-api-operation-keyed-by-what-the-choice-wrote.md)
fixed the shape of an undo: a preview and an apply keyed by what the choice
wrote, a digest of the bytes the plan rests on, one lock, one commit, no queue
row touched.

A decline has a CLI inverse (`contradictions --reopen`), keyed by the pair and
the merged-body discriminator. A reconcile has none: `reconcile` refuses to
change an existing resolution and tells the person to edit the pages or revert
with git. What it writes is spread over both pages: the edge (two for
`reconciled_with`), a `## Reconciliation` note on each page, and for a
supersede the counterpart's out-of-date mark. `unrelate`, which ADR-0067
exposes, removes one edge and withdraws the mark, but leaves the notes, so after
it a page would still say "Superseded by ..." about a page that no longer
supersedes it.

## Decision

1. **A decline is reopened in ADR-0067's shape.** `declined_contradictions`
   lists the declined records; `preview_reopen_contradiction` /
   `apply_reopen_contradiction` take the pair and `merged_absorbed_id` and make
   the `contradictions --reopen` write.
2. **A reconcile is undone by the pair.** `reconciled_pairs` lists the pairs
   holding one resolution; `preview_unreconcile` / `apply_unreconcile` take the
   pair only, and the resolution is read from the pages. A pair holds at most
   one resolution, so the pair names it; a pair holding disagreeing ones, which
   only a hand edit makes, is refused.
3. **The inverse is derived from the write.** The undo removes every edge of
   the resolution's type between the two pages, through `unrelate`'s prepare;
   withdraws a supersede's mark exactly when `unrelate` would; removes, on each
   page, the reconciliation note anchored on the other page when it keeps the
   shape `reconcile` wrote (heading, anchor, one sentence); adds one `log.md`
   line; and commits the changed pages and the log once. A note edited around
   its anchor is left as the person left it.
4. **Two refusals:** `not_declined` and `not_reconciled` join `NotUndoable`; a
   moved digest is `UndoStale`.

## Consequences

- The app can offer Undo on every contradiction and revision confirmation, and
  the "edit the pages or revert with git" advice has an app equivalent.
- After an undo with nothing in between, pages in the engine's form return to
  their earlier bytes, so a persisted contradiction verdict is fresh again and
  the finding comes back on the next check without a model call. A hand-written
  page keeps its values but is re-serialised, as every engine write does. Like every ADR-0067 undo,
  it does not come back at once: no queue row is proposed.
- The engine gains a write the CLI does not have. `reconcile`'s refusal text is
  unchanged; a CLI verb can reuse the same service later.
- Removing a note depends on its shape. A reconcile note a person rewrote stays,
  and the preview's `pages_changed` shows which pages change.

## Alternatives considered

- **Offer `unrelate` as the undo of a supersede.** No new operation, but it
  leaves both notes, and a symmetric reconcile needs two unrelates and still
  leaves its notes. Rejected: the undo would not undo what the app said it did.
- **Record what each reconcile wrote and replay it backwards.** Exact for
  reconciles the app made, but a second record of what the pages already say,
  absent for every reconcile the CLI made. Rejected.
- **Key the undo by the decision key, as the reconcile was applied.** The row
  was resolved when it was applied, and the CLI reconciles with no row
  (ADR-0067's reason). Rejected.
- **Refuse when a note was edited.** Safer for the note, but it would leave the
  edge, which the person did not edit, impossible to undo from the app.
  Rejected: the edge and the mark are undone, the edited note is kept.
