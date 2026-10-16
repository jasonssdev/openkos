---
type: Decision
title: "ADR-0063: An identity group is one decision that merges the marked members pairwise into a chosen survivor"
description: A judged-SAME identity group of more than two members, or of members of different types, is resolved through the decision surface by a new merge_group action whose selection (survivor and marked members) travels inside the version token; the commit checks once, holds the lock once, merges pairwise in one commit, keeps what landed on failure, and records keep-distinct rulings for unmarked members.
status: Proposed
date: 2026-10-16
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-16T00:00:00Z
sensitivity: public
---

# ADR-0063: An identity group is one decision that merges the marked members pairwise into a chosen survivor

- **Status:** Proposed
- **Date:** 2026-10-16

## Context

The curation decision surface ([ADR-0053](0053-curation-decisions-are-a-two-phase-application-surface.md)) merges only a judged-SAME identity item of exactly two members of one OKF type. A group of more than two members (`more_than_two_members`) and a cross-type pair (`cross_type`) list with merge withheld, and only the CLI resolves them: `adjudicate` prints the pairwise `openkos merge` commands for a larger group, and `merge --include-cross-type` is the deliberate opt-in for a cross-type pair. The desktop app reaches the engine only through `openkos.api` ([ADR-0060](0060-the-stable-python-api-is-a-versioned-facade-with-one-error-root.md)) and the local HTTP API, so a person whose list holds such a group cannot settle it, against the MVP 6 exit criteria. The maintainer decided on #1430 to build the path in MVP 6: the person marks which members are the same and which one stays, the engine merges them pairwise into that survivor, each step reversible with `unmerge`; for a cross-type group the person chooses what the thing is and the member of that type survives, never automatically.

The forces:

- **`API_VERSION` 1 is frozen and additive only.** The surface classifier treats a widened non-`Literal` union (a new plan type, a new outcome type) as breaking, so any shape that adds a variant to `DecisionPreview.plan` or `ApplyOutcome` costs `API_VERSION` 2 and a `/v2` prefix.
- **The token is the only state a stateless caller holds** (ADR-0053). A person's choice of members and survivor is new input the open row cannot supply, and a client must not be able to preview one choice and apply another.
- **No lock across human time, short lock otherwise** ([ADR-0036](0036-lock-a-short-commit-phase-in-a-per-user-state-directory.md)). A group is N-1 merges; the daemon and other applies must not interleave between them, and a busy workspace must not leave a group half-written.
- **A pairwise merge retires every row naming the absorbed concept as `stale`,** which would leave the group's own row stale although a person decided it.
- **A keep-distinct ruling matches an exact member set,** so which sets are recorded decides which questions come back.
- **One `type_alternative`.** A survivor records one runner-up type; a second absorbed type is discarded (#1293).
- **Identity is never bulk and never automatic** ([ADR-0034](0034-identity-auto-merge-only-for-a-measured-class.md)), and the surface never computes a judgment ([ADR-0059](0059-judging-is-a-quote-bound-application-surface-that-persists-per-chunk-and-never-merges.md)).

## Decision

1. **A new action, `merge_group`,** is offered on a judged-SAME identity item exactly where `merge` is withheld for `more_than_two_members` or `cross_type`; `merge` stays withheld there with the same reason. For an unjudged, DIFFERENT or UNCERTAIN item of that shape, `merge_group` is withheld with the same reason as `merge`. The engine never chooses a survivor for it.
2. **The selection travels in the version.** A `GroupSelection(survivor_id, member_ids)` is passed to preview and carried in `DecisionVersion.selection`; the version's content digest covers it, every byte each step's plan rests on, and the unmarked members' bytes. An altered selection is `Stale`; an invalid one is `NotApplied("invalid_selection")`.
3. **The plan is a `MergePlan` of kind `merge`,** as `relate_reversed` plans a `relate`, with additive fields listing every pairwise step (absorbed member, its type, whether that type is recorded as `type_alternative` or discarded) and the members kept distinct. No public union is widened, and `API_VERSION` stays 1.
4. **One check, one lock, one commit.** The token is checked once before the commit section and once inside it, before any write; the whole group runs inside one commit section, absorbed members in ascending Concept ID order, later steps prepared against the bytes the earlier ones wrote; one git commit holds the decision. Each step writes its own ledger entry, so each is reversible with `unmerge`.
5. **The group's row resolves `applied` after the first write,** as proposed when every member was marked and modified otherwise, before rows naming the absorbed member are retired.
6. **A failure after the first step keeps what landed.** The completed steps are committed and a `MergeGroupInterrupted`, a `WriteFailed`, is raised with them; no ruling is recorded.
7. **Unmarked members stay as they are,** and keep-distinct rulings are recorded over the survivor and each unmarked member, and over the survivor and all of them when two or more remain; fail-open, like keep-distinct.

This applies ADR-0018, ADR-0021, ADR-0034, ADR-0036, ADR-0037, ADR-0053, ADR-0059 and ADR-0060, and supersedes none of them.

## Consequences

**What becomes easier:**

- A person who never used a terminal can settle every identity decision the queue holds, including larger groups and cross-type pairs.
- The cross-type consent becomes explicit data: the only route is a named survivor, and the type outcome is in the plan before consent.
- Existing clients see the list exactly as before; the new action is an addition beside the withheld `merge`.
- The group stops coming back after it is decided: the row is applied, and the rulings cover the questions the person already answered.

**What becomes harder, and what we accept:**

- **The lock is held for N-1 merges.** No model call runs inside it, but the hold grows with the group and the bundle; it is measured, not bounded by construction.
- **A partly combined group is possible** on a write failure after the first step. It is reported and each completed step is reversible, but the person sees a different state from the one they chose.
- **Per-step document counts in the preview are approximate:** they are computed against the bundle before the first step, so a document mentioning two absorbed members counts in both.
- **`MergePlan` now has two readings,** a pair and a group, told apart by the action and the length of `steps`. A dedicated plan type would read better and is what a future `API_VERSION` 2 can introduce.
- **The CLI's `curate` walk does not gain the group screen;** its capability is at parity through pairwise `merge`, and its ergonomics are a follow-up.
- **Undo from the app still needs an API unmerge,** which no combine has yet.

## Alternatives considered

- **Reuse `merge` with optional survivor and member parameters.** The list could no longer say whether `merge` is offered, and the withheld reasons clients already render would become false. Rejected.
- **A new `MergeGroupPlan` with its own `kind`.** Clearest on the wire, but it widens the plan union, which costs `API_VERSION` 2 for one action. Rejected.
- **Pass the selection to apply instead of the version.** Breaks the stateless-token rule and needs the digest compared twice to stop a swapped selection. Rejected.
- **One token, lock and commit per step.** Shorter holds, but another writer could land between steps, and a busy workspace could stop the group halfway. Rejected.
- **Roll back completed steps on failure.** A second destructive sequence in a failure path, for steps that are already individually reversible. Rejected.
- **Pairwise rulings only, remainder ruling only, or no ruling.** Each either asks the person a question they already answered or records nothing they said. Rejected in favor of the pairwise rulings plus the remainder.
- **Leave it as a known gap until after MVP 6.** The maintainer rejected it on #1430.
