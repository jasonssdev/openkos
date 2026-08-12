# `contradictions` — the judge harness issue #558 needed

Issue #558: the contradiction judge read antonymy (two concepts defined in
opposition) as factual contradiction — two of three findings in a real run
were false positives — and both the true date collision and the
definitional antonyms scored confidence 1.00, so the high-confidence gate
carried no discriminating information. Nothing scored this judge, so a
prompt fix would have been adopted on intuition, which this project has
already paid for (`evals/edge_typing/README.md`).

```bash
python evals/contradictions/run_contradictions_eval.py --arm baseline --runs 5
python evals/contradictions/run_contradictions_eval.py --arm treatment --runs 5
```

`baseline` always runs the LIVE production prompt; `treatment` swaps in
`contradiction_prompts.TREATMENT_SYSTEM_PROMPT`. The runner drives the real
`find_contradictions` path — graph build, candidate seeding, prompt
assembly, fail-closed parse — over a 12-pair constructed fixture
(`contradiction_fixtures.py`): 4 factual contradictions, 5 antonym pairs,
2 plain-consistent pairs, 1 definitional-phrased contradiction.

## What it measures

**TP retention** (contradiction-class pairs judged `contradicts`) against
**antonym FP rate** (antonym pairs judged `contradicts`) — the fix must cut
the second without touching the first. Plus overall accuracy, per-pair
stability, and confidence separation (mean confidence on correct vs wrong
verdicts).

## Fixture construction traps (learned here)

The first fixture draft let every antonym doc self-describe as "the
complementary family" / "the opposite strategy" — and the baseline scored
an innocent 0.07 antonym FP rate, because the disarming phrase hands the
judge the verdict. Organic corpora define each side in opposition WITHOUT
meta-commentary; stripping those phrases reproduced the field failure at
0.40. If a fixture body explains the label, the fixture measures nothing.

## What it found (`qwen3:8b`, 5 runs per arm, 12-pair fixture)

| arm | antonym FP | TP retention | accuracy | conf. correct/wrong |
| --- | --- | --- | --- | --- |
| original prompt (stamp `20260812T220759Z`) | **0.40** | 1.00 | 0.83 | 1.00 / 1.00 |
| adopted v1 (stamp `20260812T221045Z`) | **0.24** | 1.00 | 0.90 | 0.98 / 1.00 |
| rejected v2 (stamp `20260812T221402Z`) | 0.28 | 1.00 | 0.88 | 0.98 / 1.00 |
| adopted v1, post-adoption confirmation (stamp `20260812T223612Z`) | 0.28 | 1.00 | 0.88 | 0.97 / 0.99 |

One antonym verdict flipping across a 5-run arm moves the FP rate by 0.04,
so treat 0.24 and 0.28 as the same number: the adopted prompt sits at
0.24–0.28 against the original 0.40, with TP retention pinned at 1.00 in
every measured arm.

- **Adopted (v1)**: adds the same-subject/same-property definition, the
  antonymy carve-out, and a confidence-calibration sentence — two sentences
  plus one clause, nothing else moved. The field FP pair
  (personalized/non-personalized recommendation) flips to `consistent`.
- **Rejected (v2)**: additionally asked "do the two documents describe the
  SAME thing?" — measured WORSE (0.28), consistent with
  `evals/extraction_cap`'s finding that longer prompts lose. Kept in git
  history only.
- **Residual, known**: allowlist/denylist — opposing DEFAULTS phrased as
  parallel claims — still judged `contradicts` 5/5 at confidence 1.00 by
  every arm. At this model size the judge binds "the default" as a shared
  subject. A per-task model override (`models: {contradiction: ...}`)
  exists if a larger judge is ever measured worth it.
- **Calibration is not rescuable by prompt**: stated confidence carries no
  correctness signal (0.98 correct vs 1.00 wrong after the fix — wrong
  verdicts are the MOST confident). This is why the fix targets emission,
  not thresholding, and why `--all`'s display gate must not be trusted as a
  precision knob.

## Never compare arms on different fixtures

The 0.07 → 0.40 baseline move above came entirely from a fixture change.
Re-run BOTH arms whenever `contradiction_fixtures.py` changes.
