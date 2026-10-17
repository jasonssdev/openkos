# Design: Walk An Identity Group In `curate`

## Decision 1: The walk reuses the decision surface's token path

The CLI pair walk commits through `commit_merge_decision` with `expected=None`.
The group commit has no baseline-only path (ADR-0063: the token is mandatory), so
the walk calls `preview_decision(root, layout, key, Action.MERGE_GROUP,
selection=...)` and commits the returned preview with
`commit_merge_group_decision(..., expected=version)`, exactly as `apply_decision`
does. The decision key is `identity_decision_key(group.member_ids)`, the key
`enqueue_identity` gave the row earlier in the same stage. Nothing in
`application/` changes.

## Decision 2: Offer the walk only where the open row offers `merge_group`

Before prompting, the walk reads the group's item from `list_open_decisions`. When
the item is missing or does not list `merge_group` among its actions (for example
a row still pending evaluation because a confidential verdict was not persisted),
the walk prints the existing skip line (`_echo_n_gt2_skip` or
`_cross_type_skip_line`) and counts a skip, so the manual route is still named
and no prompt promises a write the surface would refuse.

## Decision 3: The questions, in order

Members are listed once, numbered in ascending Concept ID order, each with its
title and OKF type. Every question treats Enter as "decide later" and re-asks an
unrecognized answer with a notice naming the accepted answers (#398).

1. **Which are the same** (groups of more than two only): numbers separated by
   spaces or commas, `a` for all, `d` to keep all separate, `s` to decide later.
   At least two distinct numbers are required. A cross-type pair has no such
   question: both members are the candidates.
2. **What is it** (only when the marked members declare more than one readable
   type): one number per type, sorted, plus `d` (different things, keep all
   separate) and `s`. The question names the first marked member's title, after
   the app's "What is {title}?". The members of the chosen type are the survivor
   candidates.
3. **Which keeps its name** (only when more than one candidate remains): the
   number of one candidate, or `s`. A single candidate is the survivor without a
   question. The engine never picks.

Then the preview is shown (each step, its type outcome, the members kept
distinct, and the cross-source and cross-type notes) and the final question is
`[y]es / [s]kip`, Enter = s.

## Decision 4: Outcomes

- `Applied`: counted applied; the commit disclosure is printed as for a pair.
- `NotApplied` / `Stale`: counted skipped, with one stderr line naming the reason
  (a vanished member is already announced by the observer).
- `DriftDetected`: exits 3, as for a pair.
- `MergeGroupInterrupted`: names the merges kept and the step that failed, then
  exits 1. Any other `WriteFailed`: the pair walk's failure line, exit 1.
- `d` at either of the first two questions: `commit_keep_distinct` over every
  member, listed under `declined:`.
- `s`: `skip_decision`, nothing written, offered again next run.

## Decision 5: No new ADR

The prompt design is a CLI presentation choice over ADR-0063's decision, not a new
hard-to-reverse decision; ADR-0063's consequence that the CLI "does not gain the
group screen" described that change's scope, and this change is the follow-up it
names.
