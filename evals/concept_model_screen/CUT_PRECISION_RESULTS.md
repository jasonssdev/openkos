# Results: does the selector judge's cut buy precision? (#1579, step 1)

**Verdict by the registered rule: mixed or inconclusive. Both arms fall in
rule 3.** The data do not show that the cut buys precision, but they cannot
exclude a benefit of up to about 7 points.

- Cut objects are as source-supported as kept ones at the point estimate:
  - `self` judge: kept 153 of 165 (0.927), cut 128 of 138 (0.928).
  - `fixed` judge: kept 147 of 160 (0.919), cut 134 of 143 (0.937).
- Rule 1 (the cut buys precision) fails clearly: the lower bound of kept minus
  cut is -0.068 (`self`) and -0.096 (`fixed`), far under +0.05.
- Rule 2 (the cut is too strict) fails narrowly: the upper bound is +0.0689
  (`self`) and +0.0510 (`fixed`), against the registered +0.05. The cut
  precision floor of 0.80 held in both arms (0.928, 0.937).

The rules were registered in
[`CUT_PRECISION_PREREGISTRATION.md`](CUT_PRECISION_PREREGISTRATION.md), merged
before the first `gpt-oss:20b` call. This is step 1 of #1579. It changes no
production code and no default.

## Run

- The harness is `cut_precision.py` (`label`, `score`, `--self-test`).
- Only `gpt-oss:20b` (digest `f38aa0c53da5…`, the #1557 guardrail judge) was
  called, with the #1557 G1 prompt and parser. No extraction, no selector-judge
  call, no embedding. The #1557 and #1571 directories were read only; labels
  are under `~/Dev/Projects/openkos-eval-runs/cut-precision/`.
- Ollama was used by this study alone.
- **216 of 216 window calls** (154 primary, 62 secondary): 0 unparsed. No window was excluded, so the run is
  not degraded.
- Wall time: **about 36 min** (08:33 to 09:08; 2137 s in judge calls, 9.9 s
  per call on average) against the forecast of about 30 min (upper estimate
  1 h). The population was not reduced.
- One label per ceiling object, in one call per window, so cut and kept are
  judged together for both arms and every seed.
- Primary: 303 windowed ceiling objects (154 windows) of 352 ceiling objects;
  49 objects with no window were excluded as registered. 303 = 165 + 138
  (`self`) = 160 + 143 (`fixed`).
- Validity: 0 of 25 primary records skipped for a ceiling that differs from
  the recorded one. `gemma4:26b-a4b` is one sample in both arms (its judge is
  the fixed judge).

## Primary: round 1, run 1, pool DA, five extractors pooled

Strict precision = supported of labelled. Lenient = supported or partial of
labelled, reported only. Intervals: two-stage cluster bootstrap (meetings,
then windows; 10,000 resamples; seed 42) on the pooled difference
kept minus cut.

| Arm | Precision | kept | cut | kept - cut [95% CI] | Registered label |
|---|---|---|---|---|---|
| `self` | strict | 153 of 165 (0.927) | 128 of 138 (0.928) | -0.0003 [-0.0681, +0.0689] | inconclusive |
| `self` | lenient | 164 of 165 (0.994) | 134 of 138 (0.971) | +0.0229 [-0.0225, +0.0799] | not decided |
| `fixed` | strict | 147 of 160 (0.919) | 134 of 143 (0.937) | -0.0183 [-0.0963, +0.0510] | inconclusive |
| `fixed` | lenient | 159 of 160 (0.994) | 139 of 143 (0.972) | +0.0217 [-0.0229, +0.0788] | not decided |

## Per extractor (strict, reported only)

| Extractor | Arm | kept | cut | kept - cut [95% CI] |
|---|---|---|---|---|
| `qwen3:8b` | self | 18 of 18 (1.000) | 4 of 4 (1.000) | +0.0000 [+0.0000, +1.0000] |
| `qwen3.6:35b-a3b` | self | 27 of 29 (0.931) | 24 of 26 (0.923) | +0.0080 [-0.1818, +0.1512] |
| `gemma4:26b-a4b` | self = fixed | 37 of 40 (0.925) | 25 of 29 (0.862) | +0.0629 [-0.0927, +0.2780] |
| `qwen3:14b` | self | 57 of 62 (0.919) | 39 of 42 (0.929) | -0.0092 [-0.1385, +0.1111] |
| `qwen3.6:27b` | self | 14 of 16 (0.875) | 36 of 37 (0.973) | -0.0980 [-0.3478, +0.0952] |
| `qwen3:8b` | fixed | 11 of 11 (1.000) | 11 of 11 (1.000) | +0.0000 [+0.0000, +0.0000] |
| `qwen3.6:35b-a3b` | fixed | 31 of 33 (0.939) | 20 of 22 (0.909) | +0.0303 [-0.1739, +0.2143] |
| `qwen3:14b` | fixed | 44 of 49 (0.898) | 52 of 55 (0.945) | -0.0475 [-0.2396, +0.0938] |
| `qwen3.6:27b` | fixed | 24 of 27 (0.889) | 26 of 26 (1.000) | -0.1111 [-0.2692, +0.0000] |

The extractor-level cut sets are small (4 to 55 labelled objects). `qwen3:8b`,
the production extractor, has 4 cut objects under its own judge and 11 under
the fixed judge, so nothing is decided for it alone.

## Secondary: runs 1 and 2 (strict, reported only)

| Extractor | Arm | kept | cut | kept - cut [95% CI] |
|---|---|---|---|---|
| `qwen3:8b` | self | 40 of 41 (0.976) | 13 of 13 (1.000) | -0.0244 [-0.0968, +0.0000] |
| `qwen3:8b` | fixed | 29 of 29 (1.000) | 24 of 25 (0.960) | +0.0400 [+0.0000, +0.1429] |
| `qwen3.6:27b` | self | 28 of 31 (0.903) | 80 of 85 (0.941) | -0.0380 [-0.1678, +0.0745] |
| `qwen3.6:27b` | fixed | 51 of 56 (0.911) | 57 of 60 (0.950) | -0.0393 [-0.1613, +0.0962] |

## Seed stability (strict, primary pooled)

| Seed index | Arm | kept | cut | kept - cut [95% CI] |
|---|---|---|---|---|
| s1 | self | 154 of 164 (0.939) | 127 of 139 (0.914) | +0.0254 [-0.0512, +0.1156] |
| s2 | self | 155 of 169 (0.917) | 126 of 134 (0.940) | -0.0231 [-0.0916, +0.0484] |
| s1 | fixed | 132 of 143 (0.923) | 149 of 160 (0.931) | -0.0082 [-0.0785, +0.0583] |
| s2 | fixed | 137 of 152 (0.901) | 144 of 151 (0.954) | -0.0523 [-0.1356, +0.0249] |

The difference moves between -0.052 and +0.025 across seed indices: the
judge's choice of which objects to cut is as noisy as the effect.

## What this does and does not say

- **No evidence that the cut buys precision.** In every arm and seed, the cut
  objects are supported at 0.91 to 0.95 (strict) and 0.97 (lenient), the same
  as the kept ones. At seed 0 the difference is -0.0003 and -0.018.
- **Not enough to call the cut too strict by the registered rule.** The
  upper bounds sit at +0.069 and +0.051, not below +0.05. With 138 and 143 cut
  objects, the interval is about 14 points wide.
- **Precision here is "supported by the window".** The ceiling is already
  about 0.92 supported for kept and cut alike, so there is little headroom for
  a precision-buying cut. The judge's other stated aim, dropping vague or
  near-duplicate objects, is not what G1 measures: a duplicate of a supported
  object is supported. A cut that removes duplicates would show here as
  supported-and-cut, so this measurement cannot tell a harmless duplicate cut
  from a wrongly cut distinct object.
- **Limits as registered.** One labeller, one label per object, labels from
  the ceiling batch and not the final batch (absolute levels are not
  comparable to #1557's 0.927 and 0.903), a window-only source.

## Deviations from the pre-registration

Disclosed after the fact. The pre-registration was committed at `fc44c899`;
`cut_precision.py` did not exist then, and the diff of the harness and of
these results against that commit is the two files in full. The only change
to the harness between its own first commit (`692aba70`) and the results
commit (`ec73a85b`) is the one below. No other post-registration change was
found: the margin, floor, minimum cut, bootstrap settings and rules in the
code match the registered text.

- **`gemma4:26b-a4b` reads its `fixed` records in the `self` arm.**
  - What: in `gather`, an extractor equal to the fixed judge model takes its
    kept/cut partition from the `fixed` replay records in both arms, instead
    of from the `self` records.
  - When: made after the labels had been seen (the `label` run had finished), in
    the results commit, not before the first label.
  - Why: `gemma4:26b-a4b` is both the extractor and the fixed judge, so its
    `self` and `fixed` arms are the same sample. The pre-registration says
    they are "reported once"; the harness as first committed did not enforce
    that, and the fix makes the two arms share one sample for that extractor.
  - Effect on the pooled verdict: none. Recomputed from the stored labels and
    replay records with no model call (the same `gather` and bootstrap,
    seed 42, over the four other extractors only), strict, primary, seed 0:
    - `self`: kept 116 of 125 (0.928), cut 103 of 109 (0.945), kept - cut
      -0.0170 [-0.0964, +0.0618]: rule 3, inconclusive (as registered).
    - `fixed`: kept 110 of 120 (0.917), cut 109 of 114 (0.956), kept - cut
      -0.0395 [-0.1336, +0.0334]: rule 2 on its own (the upper bound falls
      under +0.05 and the cut precision is 0.956).
    - Overall: one arm rule 2 and one rule 3, so **mixed or inconclusive**,
      the same as the reported verdict. The `fixed` arm alone would flip from
      inconclusive to "too strict" without `gemma4:26b-a4b`, so that arm's
      label depends on whether this extractor is pooled. It is pooled as
      registered ("all five extractors").
  - The pre-fix number was not computed: it would need the pre-fix path and
    its inputs for `self` records of an extractor whose `self` arm is not a
    distinct sample.
- The four-extractor figures above are a sensitivity check run from a
  throwaway script, not a registered analysis and not stored by the harness.

## Consequence for #1579

By the registered rule neither step-2 branch is established. The
measurement supports a narrower claim than "the cut is too strict": the cut
does not buy source-grounded precision at this n. Choosing between loosening
the selection and recording the lift cost as the price of precision needs a
measure the G1 label does not give (for example duplicate and vagueness
labels on the cut objects), or a larger n. No default changes, and any change
needs an ADR.
