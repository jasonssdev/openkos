---
type: Decision
title: "ADR-0034: Identity merges apply without prior consent only for an opt-in class that passed a pre-registered measurement"
description: Revises the reading of "reviewable" for one narrow class of Identity merges -- 2-member SAME, same OKF type, no cross-type concern, confidence at or above a threshold measured before shipping -- to mean announced, committed, logged and reversible after the fact; off by default; every other merge keeps per-item consent (#702).
status: Proposed
date: 2026-09-30
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-09-30T00:00:00Z
sensitivity: public
---

# ADR-0034: Identity merges apply without prior consent only for an opt-in class that passed a pre-registered measurement

- **Status:** Proposed
- **Date:** 2026-09-30

## Context

A merge absorbs one concept into another and deletes the absorbed file.
#702 therefore ruled that Identity is never accepted in bulk, and
`curate-command` encodes it: `--accept identity` is refused, `review: false`
never reaches Identity, and no flag or config value applies a merge
unreviewed. The cost is that duplicates from a large ingest, or from
re-ingest (which dedups by exact slug only), stay duplicates until a human
adjudicates each one: the maintenance the project exists to remove comes
back as a review queue (#1054).

Merges are already reversible: the ledger restores byte parity through
`unmerge` (ADR-0002, ADR-0013, ADR-0017), LIFO per survivor. What is not
established is that any class of `SAME` verdicts is safe to apply
unattended. The one measurement that exists points the other way:
`evals/adjudication/README.md` found stated confidence at 0.95 on right and
wrong verdicts alike, and a wild recurrence shape judged `same` 13 of 15
runs at 0.95.

The owner decided on #1054 (2026-09-29) that post-hoc review is acceptable
under conditions: opt-in; only 2-member `SAME` groups of one OKF type with
no `cross_type_concern`; a threshold measured in a harness with
pre-registered bars, on a fixture with hard negatives, before the mode
ships; mechanical merges (no reconciliation rewrite); at most one automatic
merge per survivor per run; one commit and one `log.md` entry per run;
#702's rule unchanged outside that subset.

## Decision

We permit Identity merges without per-item prior consent for exactly one
class, and only while all of the following hold:

1. The class passed a decision rule written down BEFORE its measurement
   (`openspec/changes/auto-merge-safe-class/design.md`), on a committed
   fixture with hard negatives, with the threshold chosen on one arm and
   judged on another. If the rule fails, no class is admitted and nothing
   is built; this ADR still stands as the condition any future class must
   meet.
2. The mode is opt-in per run and off by default.
3. The threshold and the models it was measured on are code constants,
   not user settings; a verdict from an unmeasured model is ineligible.
4. Each automatic merge is mechanical and individually reversible with
   `unmerge`; each run is one git commit with one `log.md` run entry and a
   disclosure naming every merge and its undo command.

"Reviewable" in AGENTS.md therefore means, for this class only:
announced, committed, logged and reversible after the fact. For every
other merge it still means confirmed before.

## Consequences

- Easier: a user can opt into clearing the measured-safe part of the
  duplicate queue unattended, including on a non-TTY `curate --auto` run.
- Harder: a wrong automatic merge sits in the bundle until someone reviews
  or undoes it. Undoing an earlier merge of a concept that was later merged
  again unwinds the later one too (LIFO); the one-merge-per-concept-per-run
  cap keeps a single run from stacking.
- Every change to the adjudication prompt, rubric, withdrawal markers or
  default model invalidates the measurement for the affected model and
  requires re-running the harness before the constants move.
- The measurement is on constructed, de-identified labels and one model;
  its limits are carried in the verdict file, not only here.

## Alternatives considered

- **Keep #702 absolute.** Safe, but leaves the maintenance cost unaddressed;
  the owner rejected it on #1054.
- **Accept Identity through `--accept identity` or `review: false`.** Bulk
  consent over an unmeasured class; a config value would become standing
  authorization to delete concepts.
- **Ship first, measure after.** The repo has measured and rejected five
  adjudication prompt treatments and found a 0.95-confidence wrong verdict;
  a destructive unattended mode cannot be adopted on intuition.
- **A user-configurable threshold.** Lets a workspace step outside the
  measurement; the number would no longer mean what was measured.
