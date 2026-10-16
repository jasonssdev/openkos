---
type: Decision
title: "ADR-0064: Unmerge is a two-phase API operation bound to a version over the ledger, not a queue decision"
description: openkos.api undoes a merge with preview_unmerge and apply_unmerge, keyed by the survivor and the absorbed id rather than a decision key; one operation plans a single step or the whole unwind down to the named merge, the version digests the ledger entries and every file the steps touch, and the apply checks it and runs every step under one lock, one commit per step.
status: Accepted
date: 2026-10-16
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-16T00:00:00Z
sensitivity: public
---

# ADR-0064: Unmerge is a two-phase API operation bound to a version over the ledger, not a queue decision

- **Status:** Accepted
- **Date:** 2026-10-16

## Context

The decision wording brief (`docs/decision-wording.md`) promises the app user a **Split again** undo for every combine, a pair or a group ([ADR-0063](0063-an-identity-group-is-one-decision-that-merges-marked-members-pairwise-into-a-chosen-survivor.md)). The engine undoes merges through `openkos unmerge` (the newest merge on a survivor) and `openkos unmerge --to` (tail-first down to a named merge), but the desktop app reaches the engine only through `openkos.api` ([ADR-0060](0060-the-stable-python-api-is-a-versioned-facade-with-one-error-root.md)) and the local HTTP API ([ADR-0061](0061-the-local-api-is-a-fastapi-adapter-over-the-python-api-shipped-as-an-optional-extra.md)), and neither has an unmerge (#1432).

The decision surface ([ADR-0053](0053-curation-decisions-are-a-two-phase-application-surface.md)) is the precedent for a write a stateless client consents to: a preview returns a version, the apply commits against it and refuses a stale one. But an unmerge is not a queue decision. Nothing proposes it, it has no row and no decision key, and the merge it reverses resolved its row long ago. What identifies it is the survivor's merge ledger.

## Decision

1. **Two phases, keyed by the ledger.** `preview_unmerge(survivor_id, absorbed_id)` returns an `UnmergePreview` and an `UnmergeVersion`; `apply_unmerge(version)` performs that plan. The key is the survivor and the absorbed id, both explicit, as the CLI requires; there is no decision key and the queue is not touched.
2. **One operation for a step and a chain.** The plan is always the unwind from the newest merge down to and including the named one, listed step by step in the preview. Naming the newest merge is the single step; naming a deeper one, such as a group's first absorbed member, is the chain the brief words as "the later combines on this page are split first". The preview is the consent to every step.
3. **The version digests what the plan rests on.** Its content digest covers the ledger sidecar, each step's absorbed id and stamp, every file the steps rewrite, the path each step recreates, the first step's drift baselines and the caller's `discard_survivor_edits` choice; not the time. Any change is `UnmergeStale`.
4. **One lock, one commit per step.** The apply takes the workspace lock once, re-plans from current bytes, compares the digest and runs the CLI's own single-step unmerge per entry inside it, each committed on its own. A first step that fails writes nothing and raises its refusal; a later one raises `UnmergeInterrupted` and keeps what completed.
5. **Refusals a client can word.** `survivor_missing`, `not_merged` and `survivor_edited` are returned as `NotUnmergeable`, decided from the filesystem and the ledger, never by parsing error text. Discarding a survivor edit is an explicit, versioned opt-in.

## Consequences

- The app can offer **Split again** for a pair, a group and any later stack of combines without a new state store: the ledger is the source and the version is the only state a client holds.
- A chain holds the lock for every step. No model call runs in it, and the HTTP wait stays bounded at five seconds.
- A chain can still stop part way on a check only a step's own Phase A can make (a file appearing at a later step's absorbed path, a rewrite drifted relative to the ledger). Completed steps are committed and consistent, as on the CLI.
- An unmerge proposes nothing to the queue. Whether a split from the app should also record keep-distinct (`unmerge --keep-distinct`) is left open; adding it later is additive.

## Alternatives considered

- **Make unmerge a queue decision kind.** It would need a producer to propose undos, which the engine does not do (`queue_resolution` lists `unmerge_core` as a non-resolving writer), and a row per merge forever. Rejected.
- **Separate single-step and chain operations, as the CLI's two forms.** The CLI needs two forms because it has one prompt and no preview; here the preview already lists every step, so a second function would only add a refusal.
- **A version that pins only the ledger.** Later edits to files a step rewrites would then be detected only by that step's fail-closed check, after earlier steps were written. Digesting every file the steps touch moves that refusal before any write.
- **Unlocked check, then per-step locks (the CLI's ADR-0036 shape).** The CLI checks drift per step against its own Phase A; a stateless client's version must be checked against the same bytes the writes see, so the check and the writes share one lock, as a group decision does.
