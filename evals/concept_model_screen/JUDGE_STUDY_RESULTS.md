# Results: the selector judge's loss vs the extractor's (#1571)

**Verdict: not resolved by the registered rule. The selection itself costs
lift, and the self-judging coupling matters only on `qwen3.6:27b`.**

- A fixed judge (`gemma4:26b-a4b`, the packaged `DEFAULT_JUDGE_MODEL`) still
  costs window lift on every extractor. Its loss interval excludes 0 on
  three of the four compared extractors.
- The coupling (fixed minus self) has no consistent sign:
  - `qwen3.6:27b`: +0.0146 [+0.0018, +0.0369]. Its own judge cuts more than
    the fixed judge does.
  - `qwen3:8b`: -0.0088 [-0.0198, -0.0001]. The fixed judge cuts more than
    `qwen3:8b`'s own.
- Per-extractor labels: `qwen3:8b` judge, `qwen3:14b` judge, `qwen3.6:27b`
  coupling, `qwen3.6:35b-a3b` unresolved. The rule needs 2 or more of one
  kind and no conflict, so the overall verdict is **not resolved at this n**.
- Two extractors are attributable, so a fix issue is filed: #1579. No default
  changes.

The rules were registered in
[`JUDGE_STUDY_PREREGISTRATION.md`](JUDGE_STUDY_PREREGISTRATION.md), merged
in #1578 before the first judge call.

## Run

- The harness is `judge_study.py` (`replay-self`, `run`, `score`,
  `--self-test`), committed at `c8f3daf9` before the first model call.
- **Only the selector judge was called.** Every extraction, re-ask and
  participant-capture call was served from the #1557 chat cache. The router
  raises on a miss, and there were 0 misses. The #1557 runs directory was
  not written. New calls are cached under
  `~/Dev/Projects/openkos-eval-runs/judge-study/`.
- The fixed judge's installed digest matched the pin (`001e5dafc3c7…`).
  Each self judge matched its #1557 record's digest.
- Ollama was used by this study alone. #1558 had closed, and no
  `run_window_screen` process was running.
- Scoring made no model call: `OLLAMA_HOST` pointed at a closed port, and
  embeddings came from the #1557 `bge-m3` cache through the cache-only
  guard. No embedding was missing.

### Validity checks

| Check | Result |
|---|---|
| 1. Self replay at seed index 0, all from cache, reproduces the recorded `ceiling`, `judge` and `final` stages | 35 of 35 records |
| 2. Judge input (`ceiling`) equals the recorded one in every new replay | 165 of 165 replays (105 fixed, 60 self) |
| 3. No extraction call (cache misses) | 0 |

The `none` and `self` seed-0 lifts reproduce the published #1559 table
exactly. For example, `qwen3.6:27b` gives 0.0636 → 0.0238, and `qwen3:8b`
gives 0.0390 → 0.0306.

### Wall-clock against the forecast

| Block | Forecast | Actual |
|---|---|---|
| Fixed judge (105 replays: 102 new calls, 5 cache hits) | 25 min | 6.2 min (11:46:47 to 11:52:57); 350 s in judge calls |
| Self judge, seed indices 1 and 2 (60 calls), 4 model loads | 40 min | 16.2 min (11:53:07 to 12:09:21); 926 s in judge calls |
| **Total model time** | about 65 min | **22.4 min** |

The forecast assumed every call cost its #1557 time. Seed indices 1 and 2
re-send a prompt identical to index 0's except for the seed, so Ollama's
prompt cache served most of the prompt evaluation. Those calls took 1-5 s
on `gemma4:26b-a4b`. The shortening rule was evaluated after the fixed block
(370 s measured). It kept all three self seeds.

## Primary: round 1 (n = 1, pool DA, 5 meeting-runs per extractor)

- "Lift" is window lift. For `self` and `fixed`, each window is averaged over
  three seed indices before the mean over windows.
- Objects are summed over meeting-runs. For the judge arms they are the mean
  over seeds.
- Intervals are the paired two-stage bootstrap (10,000 resamples, seed 42).
- A loss is the arm's lift minus `none`. Coupling is `fixed` minus `self`, so
  a positive coupling means the fixed judge keeps more lift.

| Extractor | none: lift (objects) | self: lift (kept) | fixed: lift (kept) | self loss [95% CI] | fixed loss [95% CI] | coupling [95% CI] | label |
|---|---|---|---|---|---|---|---|
| `qwen3:8b` | 0.0390 (31) | 0.0274 (25.3) | 0.0186 (20.0) | -0.0116 [-0.0284, +0.0000] | -0.0203 [-0.0417, -0.0028] | -0.0088 [-0.0198, -0.0001] | judge |
| `qwen3.6:35b-a3b` | 0.0531 (58) | 0.0395 (31.7) | 0.0456 (36.7) | -0.0136 [-0.0241, -0.0024] | -0.0076 [-0.0198, +0.0038] | +0.0060 [-0.0044, +0.0177] | unresolved |
| `gemma4:26b-a4b` | 0.0405 (86) | 0.0317 (56.0) | = self | -0.0089 [-0.0265, +0.0033] | = self | — (same model) | not compared |
| `qwen3:14b` | 0.0672 (112) | 0.0576 (71.3) | 0.0503 (53.3) | -0.0095 [-0.0429, +0.0057] | -0.0169 [-0.0395, -0.0012] | -0.0073 [-0.0206, +0.0087] | judge |
| `qwen3.6:27b` | 0.0636 (65) | 0.0261 (29.3) | 0.0408 (34.7) | -0.0374 [-0.0693, -0.0148] | -0.0228 [-0.0412, -0.0067] | +0.0146 [+0.0018, +0.0369] | coupling |

**Lift lost per judged-out object.** Only an object that carries a window
can move window lift. This is the loss divided by those objects (summed over
meeting-runs), and it is descriptive only.

| Extractor | self: judged out (windowed) | self: loss per object | fixed: judged out (windowed) | fixed: loss per object |
|---|---|---|---|---|
| `qwen3:8b` | 5.7 (5.7) | -2.04e-3 | 11.0 (11.0) | -1.85e-3 |
| `qwen3.6:35b-a3b` | 26.3 (26.3) | -0.52e-3 | 21.3 (21.3) | -0.36e-3 |
| `gemma4:26b-a4b` | 30.0 (30.0) | -0.30e-3 | = self | = self |
| `qwen3:14b` | 40.7 (39.3) | -0.24e-3 | 58.7 (58.7) | -0.29e-3 |
| `qwen3.6:27b` | 35.7 (35.7) | -1.05e-3 | 30.3 (30.3) | -0.75e-3 |

**Seed noise** (window lift by seed index; index 0 of `self` is the #1557
call):

| Extractor | self s0 / s1 / s2 | fixed s0 / s1 / s2 |
|---|---|---|
| `qwen3:8b` | 0.0306 / 0.0258 / 0.0258 | 0.0186 / 0.0192 / 0.0180 |
| `qwen3.6:35b-a3b` | 0.0417 / 0.0368 / 0.0401 | 0.0457 / 0.0452 / 0.0457 |
| `gemma4:26b-a4b` | 0.0308 / 0.0298 / 0.0346 | = self |
| `qwen3:14b` | 0.0559 / 0.0570 / 0.0600 | 0.0607 / 0.0446 / 0.0456 |
| `qwen3.6:27b` | 0.0238 / 0.0265 / 0.0281 | 0.0443 / 0.0374 / 0.0406 |

No judge failed in any arm (0 fail-closed fallbacks). Two fixed-judge replays needed the production retry after a `wrong_shape` first reply; both retries succeeded.

## Secondary: runs 1 and 2 (reported only)

| Extractor | none | self (kept) | fixed (kept) | self loss | fixed loss | coupling |
|---|---|---|---|---|---|---|
| `qwen3:8b` | 0.0357 (63) | 0.0275 (47.0) | 0.0231 (39.3) | -0.0082 [-0.0208, -0.0000] | -0.0125 [-0.0255, -0.0012] | -0.0044 [-0.0160, +0.0107] |
| `qwen3.6:27b` | 0.0652 (149) | 0.0273 (70.3) | 0.0447 (83.3) | -0.0380 [-0.0659, -0.0154] | -0.0205 [-0.0395, -0.0033] | +0.0175 [+0.0058, +0.0408] |

The secondary agrees in direction with the primary on both extractors. On
`qwen3.6:27b` the coupling interval excludes 0 again.

## Decision rules applied

1. **Per extractor** (the four other than `gemma4:26b-a4b`):
   - `qwen3.6:27b` is **coupling**: the coupling lower bound is +0.0018.
   - `qwen3:8b` is **judge**: no coupling, and the fixed-loss upper bound is
     -0.0028. Its coupling is negative and its interval excludes 0: the fixed
     judge is the harsher one here.
   - `qwen3:14b` is **judge**: the fixed-loss upper bound is -0.0012.
   - `qwen3.6:35b-a3b` is **unresolved**: both intervals hold 0.
2. **Overall: not resolved at this n.** Two extractors are judge-attributable,
   but one is coupling-attributable. The registered "judge" verdict requires
   no coupling, and the "coupling" verdict requires 2 or more.
3. **Follow-up: filed as #1579**, because extractors are attributable.

## What the numbers say, and what they do not

- **Some of the loss is the selection itself.** On every extractor, both
  judge arms keep less window lift than no judge. A fixed judge does not
  remove the loss; on `qwen3:8b`, the production extractor, it adds to it.
  Swapping the selector judge's model is not, by itself, a fix for the
  #1559 loss.
- **The coupling is real for `qwen3.6:27b`.** Its own judge took 0.0636 to
  0.0261. The fixed judge took the same input to 0.0408, recovering about 39%
  of the loss (+0.0146 of -0.0374). If a stronger extractor is ever adopted,
  the selector judge cannot be assumed to transfer with it. In the #1557
  screen, its final lift carried this self-judging cost.
- **The recorded `qwen3:8b` self-judge figure was its best seed.** At seed
  index 0 it was 0.0306; at indices 1 and 2 it was 0.0258. The #1557 baseline's
  final lift is therefore on the high side of its own judge's noise.
- **Lift is coverage, not quality.** The judge exists to buy precision, and
  G1 precision was not re-measured (registered as out of scope). A cut can
  lower coverage lift and still be right. Examples are a vague object whose
  description paraphrases the meeting's summary, or a near-duplicate. The fix
  issue therefore asks for the precision side before any change.

## Reproduce

```sh
uv run python evals/concept_model_screen/judge_study.py --self-test
uv run python evals/concept_model_screen/judge_study.py replay-self
uv run python evals/concept_model_screen/judge_study.py run --arm fixed
uv run python evals/concept_model_screen/judge_study.py run --arm self
uv run python evals/concept_model_screen/judge_study.py score
```

With the caches in place, `run` replays without a model call.
