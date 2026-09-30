# Archive Report: auto-merge-safe-class

**Date archived:** 2026-09-30
**Issue:** #1054
**ADR:** ADR-0034, Accepted at archive with its measurement outcome recorded
**Outcome:** stopped at the Phase 2 gate. The pre-registered rule returned **FAIL**, so the auto-apply (Phases 3–6) was not built.

## What shipped

- `evals/auto_merge/`: the measurement harness, with a model-free `--self-test`, a 26-pair constructed fixture that includes the owner's "two different meetings a week apart", the frozen `--decide` rule, and the results.
- Results: `runs-calibration-20260930T065600Z-qwen3-8b.json`, `runs-confirmation-20260930T071159Z-qwen3-8b.json`, `auto-merge-verdict-20260930-qwen3-8b.md`.

## Verdict

Each arm ran 15 runs of 26 pairs, 390 trials per arm, with `qwen3:8b`. The adjudicator reports confidence 0.95 on every `same`. It was right on 163 of 165 positives in each arm, and wrong on 20 of 225 negatives (calibration) and 21 of 225 (confirmation). The wrong calls are mostly `asym-recurrence`, but 2 per arm are `week-apart`. Because confidence carries no signal, `t*` is undefined and the rule fails. The cross-source-excluded secondary result also fails.

## Delta specs

None merged. Per task 2.6, the four conditional deltas (curate-command, entity-resolution-adjudication, entity-resolution-merge, workspace-autocommit) were deleted, since they described behaviour that is not built.
