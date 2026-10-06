---
type: Decision
title: "ADR-0049: The structural base/-N Identity class merges without prior consent under measured constants, one commit per run"
description: The structural base/-N Identity class that passed its pre-registered measurement may merge without a per-item answer on an opt-in curate run, under fixed measured constants and fresh verdicts only, in one commit per run with one reversible log entry per merge, and Identity offers an accept-recommended answer on its own bars.
status: Accepted
date: 2026-10-05
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-05T00:00:00Z
sensitivity: public
---

# ADR-0049: The structural base/-N Identity class merges without prior consent under measured constants, one commit per run

- **Status:** Accepted
- **Date:** 2026-10-05

## Context

ADR-0034 admits an Identity merge without per-item consent only for a class
that passed a decision rule written before its measurement, and records that
the first measurement (`qwen3:8b`) failed, so nothing was built. ADR-0047
then made `gemma4:26b-a4b` the default Identity judge, and #1298
pre-registered a second measurement
(`evals/auto_merge/PREREGISTRATION-1298.md`) on a narrower, structural class:
two members of one OKF type that are a base id and its ingest-time `-N`
sibling, which attach-at-ingest (ADR-0045) now rarely creates but older
bundles still hold.

That measurement passed
(`evals/auto_merge/results/auto-merge-verdict-1298-20261005T181724Z-gemma4-26b-a4b.md`):
the frozen #1054 rule found `t*` = 0.90 on the calibration arm and held on the
confirmation arm, the named negative stayed distinct, and the separate bars for
an "accept recommended" answer also passed. What remains open are the
decisions that outlive the measurement: what exactly is admitted, what makes a
run ineligible, how the merges are committed and logged, and how the pass
interacts with the workspace lock.

## Decision

1. **The admitted class.** HIGH tier, exactly two members, one OKF type
   outside `ATTACH_EXCLUDED_TYPES`, and a base/`-N` suffix family. Per group
   it also requires: no `cross_type_concern`; no confidential or LLM-blocked
   member, judged with both escape hatches off; no stacked-body guardrail
   refusal; a fresh `same` verdict at confidence `>= 0.90`; and one merge per
   survivor per run. The survivor is the base id.
2. **The constants are code constants, never settings** (ADR-0034 decision
   3), from the committed run stamps and the verdict file:

   | constant | value |
   |---|---|
   | model | `gemma4:26b-a4b` |
   | model digest | `001e5dafc3c77684c2307ebc6ab8e336e10c9b18eca52acf547d72fc83c3ca8c` |
   | `t*` | 0.90 |
   | measured at commit | `63b5f551541f7e0e98c939331898f747e23f38da` |
   | system prompt hash | `aaed5c3e06569c83` |
   | rubric digest (prompt plus withdrawal rule) | `sha256:72d7c7cb794a6ee81478d3c51cfaf88c9067f390bcc15d62759f299885ad3483` |
   | `context_window` | 12288 |
   | `max_generation_tokens` | 8192 |

   Changing any of them requires re-running the pre-registered harness. The
   rubric digest, not only the prompt hash, is checked, because the measured
   verdicts also passed through the deterministic withdrawal rule.
3. **Ineligibility.** A run is ineligible when the resolved adjudication model
   differs; the installed digest is unknown, unlisted or different; the rubric
   digest differs; `context_window` or `max_generation_tokens` is anything
   other than the measured value; or a `temperature` or `seed` is pinned (the
   measurement ran unpinned, and a pin moves the distribution `t*` was fitted
   on). An ineligible run names every failed check and the per-item flow
   stands.
4. **Fresh verdicts only.** A cached or queued verdict is never acted on,
   because neither records which model produced it.
5. **A refinement of ADR-0034 decision 4's log shape.** One git commit per run
   and one disclosure block naming every merge with its
   `openkos unmerge <survivor>`; but one `log.md` bullet per merge rather than
   one run entry, so `unmerge`'s exact-text reversal keeps working for each
   merge on its own. "One run entry" is read as "a run's merges are recorded
   together and reversible one by one".
6. **One lock across the whole run, with model-free re-planning inside it.**
   This refines ADR-0036 Decision One. Every merge changes the bundle the next
   one reads, so a plan computed before the first merge read-drifts on the
   second; and separate commit phases would let a concurrent verb's
   pathspec-scoped autocommit sweep this run's uncommitted `log.md` and
   `index.md` bullets into its own commit. No model call and no prompt is ever
   made under the lock.
7. **Accept-recommended is admitted on its own bars** (A0-A4 pass): in class,
   measured model, any confidence, a fresh verdict, confirmed by a human in
   one answer, with per-merge commits (#800). Groups with confidential or
   LLM-blocked members are excluded, pending the owner's call.
8. **Never unattended.** It is never available in the daemon or the
   pending-work queue (ADR-0037). The per-run flag on a human-invoked
   `curate` is the human-facing act, off by default and with no standing key
   (ADR-0034 decision 2, ADR-0044).

## Consequences

- Easier: an opted-in `curate` run clears the measured-safe duplicates in one
  commit and says how to undo each.
- Harder: a wrong automatic merge sits in the bundle until someone undoes it;
  an edit made to a survivor after its merge makes `unmerge` refuse unless
  `--discard-survivor-edits` is given, and the run says so.
- Every change to the adjudication prompt, rubric, withdrawal markers or
  default model, and any re-pull of the model, makes the class ineligible
  until the harness is re-run; a test fails loudly when the rubric moves.
- The model digest is read from the backend listing at run time, in memory
  only. An `openai-compatible` backend reports none, so the class is
  unavailable there.
- The workspace lock is held across the compute-free burst of a pass, a
  longer window than one merge.

## Alternatives considered

- **One run bullet in `log.md`.** Would break per-merge `unmerge` or need a
  new reversal path for "one bullet, many merges".
- **Per-merge commits for the automatic pass.** More revert granularity, but
  one disclosure and one commit per run is what ADR-0034 asked for; each merge
  is still reversible on its own.
- **Separate per-merge commit phases.** Lets a concurrent verb's autocommit
  sweep uncommitted merge writes into its own commit.
- **Acting on cached or queued verdicts.** Neither records the producing
  model, so the measurement does not cover them.
- **Checking the prompt hash only.** A withdrawal-marker edit would leave the
  class eligible on an unmeasured rubric.
