# Results: are the objects the selector judge cuts redundant? (#1579, step 1b)

Registered in `CUT_REDUNDANCY_PREREGISTRATION.md` (commit `a697d588`) before
any redundancy number was computed. Harness: `cut_redundancy.py` (`--self-test`
is model-free; mutation-checked on the core computation, 7 of 7 mutants
killed). No model was called: every embedding came from the screen's cache
(0 of 25 primary records had a missing text; `OLLAMA_HOST` pointed at a closed
port). Scores: `~/Dev/Projects/openkos-eval-runs/cut-redundancy/scores/cut_redundancy.json`.

## Verdict

**Mixed or inconclusive by the registered rule, and the data point one way:
the cut removes distinct content, not near-duplicates.** `self` meets rule 2
(distinct content); `fixed` is inconclusive only because its upper bound
(+0.1063) crosses the +0.10 margin by 0.006. In both arms about 94% of cut
objects are not near-duplicates of any kept object (self 130 of 138, fixed 135
of 143), and cut objects are no more redundant than kept ones: the difference
in near-duplicate share is +0.016 and +0.037, intervals that include 0.

## Primary: pooled, threshold 0.85, reference = kept objects of the meeting-run

25 of 25 meeting-runs measurable, both arms; seed 0.

| Arm | cut near-dup | kept near-dup | cut - kept [95% CI] | mean R cut / kept | rule |
|---|---|---|---|---|---|
| self | 8 of 138 (0.058) | 9 of 214 (0.042) | +0.0159 [-0.0378, +0.0801] | 0.702 / 0.691 | 2, distinct content |
| fixed | 8 of 143 (0.056) | 4 of 208 (0.019) | +0.0367 [-0.0135, +0.1063] | 0.731 / 0.670 | 3, inconclusive |

Overall: **mixed or inconclusive** (one arm rule 2, one rule 3).

Per extractor (not decisive; small n, several intervals degenerate at 0):

| Extractor | self cut / kept near-dup | self cut - kept | fixed cut / kept near-dup | fixed cut - kept |
|---|---|---|---|---|
| `qwen3:8b` | 1 of 4 / 0 of 27 | +0.250 [0.000, +1.000] | 0 of 11 / 0 of 19 | 0.000 |
| `qwen3.6:35b-a3b` | 1 of 26 / 0 of 32 | +0.039 [0.000, +0.167] | 1 of 22 / 0 of 36 | +0.046 [0.000, +0.231] |
| `gemma4:26b-a4b` | 1 of 29 / 4 of 57 | -0.036 [-0.148, +0.086] | same sample | same |
| `qwen3:14b` | 4 of 42 / 5 of 70 | +0.024 [-0.108, +0.167] | 6 of 55 / 0 of 57 | +0.109 [+0.028, +0.225] |
| `qwen3.6:27b` | 1 of 37 / 0 of 28 | +0.027 [0.000, +0.136] | 0 of 26 / 0 of 39 | 0.000 |

The only per-extractor interval clear of 0 is `qwen3:14b` under the `fixed`
judge (6 of 55 cut near-duplicates, +0.109), and it is not decisive.

## Not decisive

- **Threshold (pooled, primary):** at 0.80 the cut share is 23 of 138 (self,
  +0.064 [-0.042, +0.174]) and 26 of 143 (fixed, +0.100 [-0.029, +0.209]); at
  0.90 it is 3 of 138 and 3 of 143 against 0 of 214 and 0 of 208 kept (+0.022
  and +0.021, lower bounds 0.000). Even at the loose threshold, more than 81% of cut
  objects are not near-duplicates.
- **Same-window reference:** self 2 of 75 cut vs 2 of 95 kept (+0.006
  [-0.065, +0.103]); fixed 3 of 76 vs 0 of 93 (+0.040 [0.000, +0.109]).
- **Secondary (run 1 and 2):** `qwen3:8b` self 2 of 13 vs 0 of 50, fixed 1 of
  25 vs 0 of 36; `qwen3.6:27b` self 3 of 85 vs 0 of 64, fixed 2 of 60 vs 0 of
  89. Point differences +0.03 to +0.15, lower bounds 0.
- **Seeds 1 and 2 (primary pooled):** self -0.037 and -0.019, fixed +0.017
  and +0.023. The sign of the difference is not stable across seeds.

## Coverage

Measurable records 25 of 25 in both arms; objects without a reference 0
(self) and 1 (fixed); 49 objects carry no window and are included in the
meeting-run reference (excluded only from the same-window sensitivity).

## What this means for #1579 step 2

- Step 1 found the cut no better on support; this step finds it no better at
  deduplication either. Both of the two obvious things a selector judge could
  buy, source support and removal of near-duplicates of what is kept, are
  absent at this n. The cut therefore costs lift and removes mostly distinct
  content: the argument for a deterministic dedup in place of the judge
  (rather than a judge that "dedups") is stronger, and the argument for
  loosening the selection is stronger, but neither is established by a rule
  that needs the `fixed` arm too.
- Distinct is not the same as valuable. Whether the cut distinct objects are
  vague, low-value, or gold-relevant is not measured here. The optional
  L10 gold-loss split was not run: the #1559 stage-loss table is not in this
  checkout, so it is deferred rather than reconstructed.
- No default changes; any loosening needs its own ADR.

## Deviations and limits

- None from the registration: threshold, margin, cut-share bound, minimum, and
  population are as committed; the harness has no post-registration change in
  them (the rule constants in the code match the registered text).
- Near-duplicate is an embedding proxy at one registered threshold on one
  embedding model; redundancy of cut objects among themselves is not counted;
  "vague" objects are not measured.
- Small counts: 8 near-duplicates among 138 and 143 cut objects. Intervals
  are wide, and the `fixed` verdict flips on 0.006.
