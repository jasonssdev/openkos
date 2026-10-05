# auto-merge eval -- verdict (#1298, structural class)

Rule: `evals/auto_merge/PREREGISTRATION-1298.md` (approved 2026-10-05).

Calibration: `runs-structural-calibration-20261005T164453Z-gemma4-26b-a4b.json`. Confirmation: `runs-structural-confirmation-20261005T170308Z-gemma4-26b-a4b.json`.
Reference: `runs-structural-calibration-20261005T175719Z-qwen3-8b.json`, `runs-structural-confirmation-20261005T181230Z-qwen3-8b.json`.
Model `gemma4:26b-a4b` · git `63b5f551541f7e0e98c939331898f747e23f38da` · fixture digest `sha256:9f802d70e9a75d9f5665c9c9f3e4a3bd184fff84b23ab0bb4bcbc6d85f67c2ec`.

**Verdict: PASS**

`B` (highest in-class calibration negative `same` confidence): -inf (non-binding -- no negative ever reached it).
`t*`: 0.9000.

| bar | rule | value | result |
| --- | --- | --- | --- |
| R0 validity | no missing trial, one digest and model | ok | PASS |
| Step 1 | a positive above `B` | t* found | PASS |
| R1 exposure | >= 180 in-class negative confirmation trials | 240 | PASS |
| R2 false auto-merges | 0 | 0 of 240 | PASS |
| S2 named negative, both arms | 0 | 0 of 90 | PASS |
| R4 retention | >= 0.50 | 175 of 180 (0.97) | PASS |
| R5 stability | >= 0.80 | 0.97 | PASS |
| L1 latency | <= 3.0x reference median | 1.20x (73.0 s vs 60.7 s per run) | PASS |

## Identity accept-recommended (A0-A4, both arms pooled)

**Verdict: PASS**

| bar | rule | value | result |
| --- | --- | --- | --- |
| A0 validity | as R0 | ok | PASS |
| A1 named negative | 0 | 0 of 90 | PASS |
| A2 false recommendations | <= 1% | 0 of 480 | PASS |
| A3 persistence | no pair more than once | worst pair 0 | PASS |
| A4 recall | >= 0.90 | 354 of 360 (0.98) | PASS |

## Reported, never deciding

- Cross-source-excluded in-class population, #1054 rule: **FAIL** -- R1 exposure: 0 negative trial(s) reached the gate, below the floor of 180.
- Reference `qwen3:8b` on the same rule (L1 not applicable): **PASS**; B -inf, t* 0.95; accept-recommended **PASS**. Disclosed only (decision 4).

### Per-probe, in-class (both primary arms)

| probe | expected | n | same | same confidence (min-max) |
| --- | --- | --- | --- | --- |
| key-homonym | `different` | 330 | 0 of 330 | - |
| key-part-whole | `different` | 60 | 0 of 60 | - |
| key-distinct-instance | `different` | 90 | 0 of 90 | - |
| key-cross-source-dup | `same` | 240 | 238 of 240 | 1.00-1.00 |
| key-reingest-dup | `same` | 60 | 60 of 60 | 0.95-1.00 |
| key-asym-dup | `same` | 60 | 56 of 60 | 0.90-1.00 |

## Q1 secondary -- the #1054 fixture under the same model (report-only)

Files: `runs-calibration-20261005T172225Z-gemma4-26b-a4b.json`, `runs-confirmation-20261005T174159Z-gemma4-26b-a4b.json`.
Frozen #1054 rule: **PASS**; B -inf, t* 0.8.

Event base/`-N` pairs (both arms):

| probe | expected | n | same | same confidence (min-max) |
| --- | --- | --- | --- | --- |
| recurrence | `different` | 90 | 0 of 90 | - |
| asym-recurrence | `different` | 90 | 0 of 90 | - |
| event-same | `same` | 60 | 60 of 60 | 1.00-1.00 |
| asym-same | `same` | 60 | 29 of 60 | 0.90-0.95 |

All #1054 probes (both arms):

| probe | expected | n | same | same confidence (min-max) |
| --- | --- | --- | --- | --- |
| week-apart | `different` | 90 | 0 of 90 | - |
| recurrence | `different` | 90 | 0 of 90 | - |
| asym-recurrence | `different` | 90 | 0 of 90 | - |
| namesake-person | `different` | 60 | 0 of 60 | - |
| aspect-or-part | `different` | 120 | 0 of 120 | - |
| reingest-dup | `same` | 90 | 90 of 90 | 0.90-1.00 |
| event-same | `same` | 60 | 60 of 60 | 1.00-1.00 |
| person-same | `same` | 60 | 60 of 60 | 0.80-1.00 |
| alias-same | `same` | 60 | 58 of 60 | 0.90-0.95 |
| asym-same | `same` | 60 | 29 of 60 | 0.90-0.95 |

## What a PASS does not establish

Constructed labels measure rubric consistency, not agreement with a human on a real bundle. The result covers one judge model and one prompt hash. Synthetic, de-identified pairs may be easier than real documents. The class's real-world frequency after attach-at-ingest is low, so the benefit is mostly to bundles compiled before it.
