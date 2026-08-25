# `contradictions` — the judge harness issues #558 and #870 needed

Issue #558: the contradiction judge read antonymy (two concepts defined in
opposition) as factual contradiction — two of three findings in a real run
were false positives — and both the true date collision and the
definitional antonyms scored confidence 1.00, so the high-confidence gate
carried no discriminating information. Nothing scored this judge, so a
prompt fix would have been adopted on intuition, which this project has
already paid for (`evals/edge_typing/README.md`).

Issue #870 (see [its section below](#the-benefit-vs-limitation-class-870)):
a benefit and a limitation of one technique judged `contradicts` at 0.95 in
the field. Its fix was measured here, adopted, and took the #558 residual
with it.

```bash
python evals/contradictions/run_contradictions_eval.py --arm baseline --runs 15
python evals/contradictions/run_contradictions_eval.py --arm treatment --runs 15
```

`baseline` always runs the LIVE production prompt; `treatment` swaps in
`contradiction_prompts.TREATMENT_SYSTEM_PROMPT`. The runner drives the real
`find_contradictions` path — graph build, candidate seeding, prompt
assembly, fail-closed parse — over an 18-pair constructed fixture
(`contradiction_fixtures.py`): 4 factual contradictions, 5 antonym pairs,
2 plain-consistent pairs, 1 definitional-phrased contradiction, 4
benefit-limitation pairs and 2 evaluative-contradiction guards (#870).

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

## The benefit-vs-limitation class (#870)

The 0.2.9 E2E reported a benefit and a limitation of one technique (RAG
improves decision extraction / RAG loses traceability) judged `contradicts`
at 0.95 — sharper, the benefit body had already integrated the limitation
in its own prose ("Sin embargo, ..."), so the judge flagged a tension one
member resolves internally. Six pairs joined the fixture:
`benefit-limitation` ×4 (expected `consistent`; the first mirrors the wild
pair exactly, integration included) and `evaluative-contradiction` ×2 (the
guard: opposite claims about the SAME measured aspect phrased evaluatively,
expected `contradicts` — a carve-out must not wash these out).

**All prior arms in this file predate this fixture change and are not
comparable to anything below.** Both arms re-measured whole: `qwen3:8b`,
**15 runs per arm**, production client settings, 2026-08-25.

| arm | benefit-limitation FP | antonym FP | TP retention | evaluative guard | accuracy | stability |
| --- | --- | --- | --- | --- | --- | --- |
| baseline (stamp `20260825T023011Z`) | **0.15** | 0.32 | 1.00 | 1.00 | 0.86 | 0.95 |
| treatment, ADOPTED (stamp `20260825T024424Z`) | **0.00** | **0.00** | 1.00 | 1.00 | **1.00** | 1.00 |

The baseline's whole class failure sits in the wild-mirror pair: **wrong 14
of 15 runs** (9 `contradicts` at 0.95, 5 `uncertain`, stability 0.60), while
the three pairs whose benefit body and limitation body discuss DIFFERENT
properties are clean.
The failing shape is precise: both bodies acknowledge the SAME limitation —
one in passing, one in depth — so the claims agree and only the tone
opposes, and the judge read opposing tone as incompatibility.

The treatment is ONE sentence inserted after the antonymy carve-out
(benefit-vs-limitation names different properties; shared acknowledgement
of a limitation is agreement; contradicts needs incompatible values for one
property, never opposite tone). Result: a perfect arm — 270 of 270
judgements right, every pair at stability 1.00 — including **antonym FP
0.32 → 0.00**, which closes the allowlist/denylist residual the #558
section above records as unfixable at this model size: opposing DEFAULTS
phrased as parallel claims stop reading as a conflict once tone is named
as a non-property. Adopted into production
(`resolution/contradiction.py`); `TREATMENT_SYSTEM_PROMPT` now equals
production, per this module's convention.

Two caveats, stated rather than implied:

- **The class rode one pair.** The other three benefit-limitation pairs
  were already clean, so the 0.15 → 0.00 headline is one pair going from
  14-of-15 wrong to 15-of-15 right — but that pair is the wild shape, its
  wrongness was persistent, and the treatment arm is 270 of 270 across the
  whole fixture, far beyond this harness's measured self-swing (the n=5
  warning below).
- **The evaluative guards are value-anchored.** Both guard pairs carry
  citable quantities (80ms/120ms swapped; halved vs doubled), so a
  same-property conflict with NO citable values — the shape the sentence's
  "incompatible values for one property" wording would be likeliest to
  wash out — is not measured here. It was not measured before #870 either
  (the #558 prompt already demanded citable conflicting claims); stated so
  the 1.00 guard row is not read wider than its fixtures.
- **Persisted findings do not re-judge.** `finding_input_digests` covers
  member bytes and the relation label, not the rubric, so a finding stored
  under the old prompt keeps serving until its members change or
  `contradictions --fresh` re-judges. The #838 rubric-digest gate covers
  the adjudication store only.

## What a smaller model costs here (#700 lever 3) — REJECTED

[#700](https://github.com/jasonssdev/openkos/issues/700) ranked "smaller models
for mechanical tasks" third: contradiction judging is classification, not
composition, and the per-task `models:` seam already accepts a value for it.
`qwen2.5:3b` (1.9 GB) against the default, both on the **live production
prompt**, **15 runs each** (75 antonym judgements per arm), 2026-08-16, same
machine and session:

| model | antonym FP | TP retention | accuracy | stability | run latency |
| --- | --- | --- | --- | --- | --- |
| `qwen3:8b` *(default)* | **0.19** | 1.00 | 0.92 | 0.97 | **29.9s** |
| `qwen2.5:3b` | **0.33** | 1.00 | 0.86 | 0.96 | **13.2s** |

**2.3× faster, and it nearly doubles the false-positive rate** on the one metric
this harness exists to protect — the number #558 spent a prompt change pulling
down from 0.40. TP retention holds at 1.00 in both arms, so the smaller model
never *loses* a real contradiction; it invents more of them.

It was rejected on **payoff**, not on that alone. #700's own measurement is that
`curate` is interaction-bound: 90s of model time inside a 310s session, of which
this stage was 39s. Cutting it to a third saves ~22s of a session whose other
220s is the operator answering prompts. A cheaper judge that surfaces more false findings is
a bad trade at any speed — and since [#598](https://github.com/jasonssdev/openkos/issues/598)
findings persist and are ranked in `status` and `next`, so a false positive now
costs attention repeatedly instead of dying with the process.

### Five runs is not enough on this metric, and that is the finding

The table above says 15 runs because **5 could not tell the two models apart**.
Measuring `qwen3:8b` against itself — identical model, identical prompt,
identical client settings, minutes apart — produced:

| sample | runs | antonym FP |
| --- | --- | --- |
| `runs-baseline-20260816T084135Z-qwen3-8b.json` | 5 | **0.44** |
| `runs-baseline-20260816T084923Z-qwen3-8b.json` | 15 | **0.19** |

A 0.25 spread between two samples of one arm is larger than the 0.14 gap the
15-run table then reports between two different models. A first 5-run pair, run
before these and not retained, had put the default at 0.28 and the small model
at 0.36 — a tidy, publishable, **meaningless** result, and the conclusion drawn
from it (that the smaller model costs "two verdict flips") did not survive the
larger sample. It is described rather than filed on purpose: keeping a result
this size invites someone to cite it.

This README already warned that one flip moves the rate by 0.04 on a 5-pair
antonym set. That understates it: with 5 runs the arm is 25 judgements, and both
the model and the run order move several of them. **Do not compare arms here at
n=5** — and do not read a single exploratory run at all: one such run of
`qwen2.5:3b` scored 0.20, better than the default it in fact loses to by 14
points.

## Never compare arms on different fixtures

The 0.07 → 0.40 baseline move above came entirely from a fixture change.
Re-run BOTH arms whenever `contradiction_fixtures.py` changes.

**Nor on different client settings.** Until #700 this runner built
`OllamaClient(model=...)` with no generation ceiling and no context window, so
it measured every model under conditions `ingest` never runs it in. It now
passes production's own `DEFAULT_MAX_GENERATION_TOKENS` and
`DEFAULT_CONTEXT_WINDOW`. Arms recorded before that change are not comparable
with arms recorded after it — which is why the table above re-measures the
default rather than reading its number off the #558 sweep.
