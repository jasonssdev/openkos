---
type: Decision
title: "ADR-0067: Undoing a review choice is a two-phase API operation keyed by what the choice wrote"
description: openkos.api undoes keep separate, a connection and a how-quickly setting with a preview and an apply per kind, keyed by the member set, the edge or the type rather than a decision key; each apply re-plans under one lock against a version digest, resolves no queue row, and a tier undo restores the configured entry, removing it when there was none.
status: Proposed
date: 2026-10-16
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-16T00:00:00Z
sensitivity: public
---

# ADR-0067: Undoing a review choice is a two-phase API operation keyed by what the choice wrote

- **Status:** Proposed
- **Date:** 2026-10-16

## Context

The decision wording brief (`docs/decision-wording.md`) offers an **Undo** on the
confirmation of four choices. A combine has one, through `preview_unmerge` and
`apply_unmerge` ([ADR-0064](0064-unmerge-is-a-two-phase-api-operation-bound-to-a-version-over-the-ledger.md)).
Keep separate, connect and "how quickly it goes out of date" have none in
`openkos.api` (#1466), although the CLI reverses each (`duplicates --reopen`,
`unrelate`, `set-volatility`). The desktop app reaches the engine only through
the API ([ADR-0060](0060-the-stable-python-api-is-a-versioned-facade-with-one-error-root.md),
[ADR-0061](0061-the-local-api-is-a-fastapi-adapter-over-the-python-api-shipped-as-an-optional-extra.md)).

Three forces shape the operations. The choice's queue row was resolved when it
was applied, and the same ruling, relation or tier can be written by the CLI
with no row, so a decision key cannot name what to undo. A stateless client must
consent to exactly what is written, as for every API write
([ADR-0053](0053-curation-decisions-are-a-two-phase-application-surface.md)).
And a tier undo has no CLI precedent for "there was no entry": `set-volatility`
only sets one.

## Decision

1. **Two phases per kind, keyed by what the choice wrote.** `preview_reopen` /
   `apply_reopen` take the member set, `preview_unrelate` / `apply_unrelate` the
   edge `(source, type, target)`, `preview_tier_change` / `apply_tier_change` the
   type and the tier. No decision key is involved and the queue is neither
   resolved nor proposed to; a reopened group comes back when the queue is next
   refreshed.
2. **The version digests what the plan rests on.** The ruling's sidecar, the
   source page (and the target when its deprecated-status export is withdrawn),
   or `openkos.yaml`, with the key. The apply takes the workspace lock once,
   re-plans from current bytes, and returns `UndoStale` on a moved digest, then
   writes and commits only its own paths: one write, one commit.
3. **One refusal type for the three kinds.** `NotUndoable` with
   `not_kept_distinct`, `not_related`, `tier_unchanged` or `unknown_type`,
   decided from the files, never from error text, and one HTTP row per reason.
4. **A tier undo restores the configured entry.** A set-tier preview reports the
   type's entry before the change (`configured_tier`); the undo sets it back, and
   removes the entry when there was none (`tier=None`), by the same
   comment-safe text surgery `set-volatility` uses.
5. **Listing and reading stay free.** `kept_distinct` lists the rulings a
   settings screen will offer **Ask me again** on, and `merged_concepts` (#1467)
   lists every combined page; both read sidecars only, with no lock and no model.

## Consequences

- The app can offer the brief's Undo for all four choices, and later the
  settings and page screens can reuse the same operations for **Ask me again**,
  **Remove** and the tier setting.
- One refusal type keeps the surface smaller than a refusal and a stale type
  per kind would, at the cost of a reason set that is a union across kinds.
- An undo can reverse a ruling, relation or tier the CLI wrote, not only one the
  app wrote. That is the CLI verbs' scope too, and the preview names exactly
  what is reversed.
- `openkos.yaml` gains a write the CLI does not have (removing an entry). It is
  text surgery with the same refusals as setting one; a header left with no
  entries is kept.
- Like every API write, an undo refreshes no derived index.

## Alternatives considered

- **Undo by decision key, reopening the queue row.** The row is resolved, the CLI
  writes the same files with no row, and a resolved row would have to be
  revived. Rejected for the reason ADR-0064 rejected it for unmerge.
- **A per-kind refusal and stale type.** Six names for four reasons, and six
  HTTP rows for one meaning of stale. Rejected.
- **Restore a tier by writing the old effective tier explicitly.** The effect
  today is the same, but it pins a type that had followed the engine's default,
  so a later default would no longer reach it. Rejected.
- **Unlocked check, then a locked write with drift guards (the decision
  surface's shape).** Each undo is one small write; planning under the lock makes
  the plan and the write see the same bytes with no second guard, as ADR-0064
  chose for unmerge.
