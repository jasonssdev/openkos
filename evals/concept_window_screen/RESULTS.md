# Results: choosing the concept-extraction window size (#1558)

**Verdict: NO WINNER. `_CHUNK_TARGET` stays at 4,000. Nothing is adopted, and
no ADR is written.** The rules are in [`PREREGISTRATION.md`](PREREGISTRATION.md),
which merged in #1573 before any scored call. On `qwen3:8b` (the primary
model) no larger window improved the primary metric:

- 6,000 and 12,000: confidence intervals straddle zero, with negative points.
- 8,000: worse with a CI that excludes zero.
- 12,000 also fails V1 (failures) and V4 (volume).

The exploratory model `qwen3.6:27b` was **not run** past its stage 0: its
forecast was 4.04 h against the registered 4.0 h budget.

## Run

- Dates: 2026-10-19, 05:11 to 10:40 (EST). The harness was at `7f0a7689`, the
  registration merge. Production `extract_concept_union` ran unpatched apart
  from `_CHUNK_TARGET`. `num_predict` was 8192, `think` false, `seed = r`.
  Model digests match the registration.
- Before each step, the driver waited until no other eval harness process was
  running. Ollama was used by this screen alone while it ran.
- Raw runs, caches and judge replies are kept outside both repositories, in
  `~/Dev/Projects/openkos-eval-runs/window-screen/`.

### Reuse of #1557's cache: valid

`--replay-only` replayed 4,000-char runs from #1557's cache:

- `qwen3:8b`: seeds 1 and 2 on DA, and `medium-10` seed 1;
- `qwen3.6:27b`: the same.

Every call was a cache hit, 108 of 108 for `qwen3:8b`. For `qwen3:8b`, all 77
stage lists (title and window, every stage, every source-run) are identical
to #1557's stored records. Seed 3 and `medium-10` seeds 2 and 3 were new
calls.

## Stage 0 (per arm; no quality read)

| Model | Arm | Stage-0 window seconds | Median | Window timeout | Largest prompt (tokens) | `num_ctx` | Feasible |
|---|---|---|---|---|---|---|---|
| `qwen3:8b` | 4,000 | (#1557) | 36.5 | 146 s | 2,973 | 12288 | — |
| `qwen3:8b` | 6,000 | 47.2, 52.9, 56.6 | 52.9 | 211 s | 3,426 | 12288 | yes |
| `qwen3:8b` | 8,000 | 7.5, 72.4, 72.6 | 72.4 | 290 s | 3,985 | 12288 | yes |
| `qwen3:8b` | 12,000 | 82.2, 77.4, 9.8 | 77.4 | 310 s | 5,083 | **16384** | yes |
| `qwen3.6:27b` | 6,000 | 106.7, 93.6, 63.3 | 93.6 | 374 s | 3,524 | 12288 | yes |
| `qwen3.6:27b` | 8,000 | 49.2, 109.1, 83.8 | 83.8 | 335 s | 4,113 | 12288 | yes |

At 12,000 the largest stage-0 prompt was 5,083 tokens, which passes the
5,000-token bound. So, as registered, that arm ran with `num_ctx` 16384. The
pipeline's own planning window stayed 12288.

**12,000 ran.** Its stage 0 was feasible, and the forecast for its three
runs was 1.10 h, within the 1.5 h bar.

## Validity check M1: passed

The 4,000 arm's meeting-level lift (n = 3, bootstrap over the 5 meetings) was
**+0.0098 [+0.0031, +0.0174]**. The lower bound is above 0, so the embedding
lift stayed the primary. Judge coverage is reported only.

## Primary model: `qwen3:8b` (n = 3, pool DA plus `medium-10`)

"dlift" is the paired meeting-level delta of the primary: arm minus the
4,000 arm, over 5 meetings, with a bootstrap of 10,000 resamples and seed 42.

| Arm | DA windows | meeting dlift [95% CI] | meeting lift (mean) | failed chars (V1) | failed windows | objects / meeting-run (V4) | verbatim (V5) | `medium-10` wrong-language titles (V3) | redundant final (F) |
|---|---|---|---|---|---|---|---|---|---|
| 4,000 | 41 | — | 0.0098 | 9,714 of 451,260 (0.022) | 3 of 123, all unparseable | 4.60 (69 in 15) | 0 of 69 | 0 of 24 | 9 of 69 (0.130) |
| 6,000 | 27 | -0.0044 [-0.0124, +0.0036] | 0.0054 | 6,042 of 451,302 (0.013) | 3 of 81, all unparseable | 2.93 (44 in 15) | 0 of 44 | 0 of 25 | 2 of 44 (0.045) |
| 8,000 | 22 | **-0.0143 [-0.0237, -0.0064]** | -0.0045 | 0 of 451,317 (0.000) | 0 of 66 | 2.40 (36 in 15) | 0 of 36 | 0 of 26 | 3 of 36 (0.083) |
| 12,000 | 15 | -0.0036 [-0.0095, +0.0039] | 0.0062 | 21,059 of 451,338 (**0.047**) | 2 of 45 (1 unparseable, 1 capped) | **2.20** (33 in 15) | 0 of 33 | 0 of 12 | 2 of 33 (0.061) |

Per-meeting deltas (arm minus 4,000, mean over 3 runs):

| Arm | ES2015a | IS1004b | TS3009c | ES2015d | IS1004d |
|---|---|---|---|---|---|
| 6,000 | -0.0130 | +0.0020 | -0.0030 | +0.0084 | -0.0166 |
| 8,000 | -0.0034 | -0.0184 | -0.0317 | -0.0109 | -0.0071 |
| 12,000 | -0.0128 | -0.0051 | +0.0104 | -0.0038 | -0.0065 |

### Reported beside the primary (not decided on)

| Arm | judge coverage (mean) | judge-coverage delta [95% CI] | G1 precision | G1 delta [95% CI] | window lift (unpaired) | `medium-10` subject recall, exact | `medium-10` final objects per run | #699 repeats on `medium-10`, raw / final, per run | median s / window call | s / meeting-run | total s (DA) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 4,000 | 0.255 | — | 56 of 60 (0.933) | — | 0.0300 | 22 of 33 (0.667) | 7, 7, 10 | 0,0,0 / 0,0,0 | 34.3 | 307.0 | 4,605 |
| 6,000 | 0.221 | -0.0341 [-0.1051, +0.0253] | 25 of 28 (0.893) | -0.0405 [-0.2424, +0.0599] | 0.0166 | 24 of 33 (0.727) | 10, 7, 8 | 0,0,0 / 0,0,0 | 48.0 | 272.8 | 4,092 |
| 8,000 | 0.205 | -0.0504 [-0.1109, +0.0185] | 24 of 26 (0.923) | -0.0103 [-0.0784, +0.0429] | -0.0010 | 19 of 33 (0.576) | 4, 11, 11 | 0,1,0 / 0,1,0 | 65.3 | 263.7 | 3,956 |
| 12,000 | 0.129 | **-0.1261 [-0.1789, -0.0733]** | 16 of 17 (0.941) | +0.0078 [-0.0181, +0.0483] | 0.0097 | 12 of 33 (0.364) | 4, 4, 4 | 0,0,0 / 0,0,0 | 90.2 | 332.0 | 4,980 |

Own vs foreign coverage curve (final objects, meeting level, mean share of
gold with best cosine ≥ tau):

| Arm | 0.50 | 0.55 | 0.60 | 0.65 | 0.70 | 0.75 |
|---|---|---|---|---|---|---|
| 4,000 own / foreign | 0.642 / 0.603 | 0.419 / 0.385 | 0.222 / 0.164 | 0.102 / 0.074 | 0.029 / 0.018 | 0.003 / 0.007 |
| 6,000 own / foreign | 0.504 / 0.481 | 0.315 / 0.292 | 0.147 / 0.121 | 0.033 / 0.051 | 0.007 / 0.028 | 0.000 / 0.007 |
| 8,000 own / foreign | 0.452 / 0.486 | 0.214 / 0.274 | 0.127 / 0.124 | 0.049 / 0.061 | 0.014 / 0.022 | 0.000 / 0.011 |
| 12,000 own / foreign | 0.406 / 0.350 | 0.256 / 0.247 | 0.119 / 0.090 | 0.033 / 0.027 | 0.006 / 0.015 | 0.000 / 0.011 |

From 0.80 up, every arm covers no own gold at all (0.000).

Per-section soft coverage (abstract / decisions / problems):

| Arm | Coverage |
|---|---|
| 4,000 | 0.566 / 0.513 / 0.458 |
| 6,000 | 0.535 / 0.492 / 0.443 |
| 8,000 | 0.468 / 0.429 / 0.384 |
| 12,000 | 0.450 / 0.429 / 0.373 |

Per-stage loss, meeting-level lift (objects summed over the 15 meeting-runs in brackets):

| Arm | raw | dedup | drops | +additions | ceiling | judge | final |
|---|---|---|---|---|---|---|---|
| 4,000 | 0.0136 (118) | 0.0140 (116) | 0.0114 (82) | 0.0117 (91) | 0.0117 (91) | 0.0098 (69) | 0.0098 (69) |
| 6,000 | 0.0138 (83) | 0.0136 (80) | 0.0149 (43) | 0.0107 (59) | 0.0107 (59) | 0.0054 (44) | 0.0054 (44) |
| 8,000 | 0.0076 (68) | 0.0061 (63) | -0.0042 (29) | -0.0041 (39) | -0.0041 (39) | -0.0045 (36) | -0.0045 (36) |
| 12,000 | 0.0036 (47) | 0.0034 (46) | 0.0046 (20) | 0.0074 (37) | 0.0074 (37) | 0.0062 (33) | 0.0062 (33) |

### `num_ctx` feasibility, by call kind

Each cell is: calls; the largest prompt in tokens; the largest prompt plus
reply; how many calls exceeded `num_ctx`; and the `done_reason` counts.

| Arm | window calls | participant capture | selector judge | re-ask |
|---|---|---|---|---|
| 4,000 | 135; 2,973; 6,250; 0 over; stop 135 | 18; 6,186; 14,071; **2 over 12288**; stop 17, length 1 | 18; 7,467; 7,512; 0 over | — |
| 6,000 | 90; 3,515; 8,547; 0 over; stop 90 | 18; 6,567; 14,051; **2 over**; stop 16, length 2 | 18; 7,747; 7,774; 0 over | 4; 3,963; 5,960; 0 over |
| 8,000 | 72; 4,099; 9,680; 0 over; stop 72 | 18; 6,062; 13,201; **1 over**; stop 17, length 1 | 16; 7,152; 7,241; 0 over | 1; 3,961; 4,112; 0 over |
| 12,000 (`num_ctx` 16384) | 54; 5,119; 13,129; 0 over; **stop 50, length 4** | 18; 6,065; 13,661; 0 over; stop 18 | 9; 6,065; 6,090; 0 over | 9; 6,110; 6,112; 0 over |

- **Every window call fit its `num_ctx` in every arm.**
- The over-budget calls are participant-capture runaways that reached
  `num_predict` 8192 on a 6 K-token prompt. That is the existing #829/L5
  regime, it is present at 4,000 too, and the window size does not cause it.
- At 12,000, 4 window calls ran away to `num_predict` (done_reason
  `length`), in 3 windows. One window (IS1004d, seed 3) capped on both
  attempts and was lost; the other 2 recovered on the production retry. No
  window call ran away at 4,000, 6,000 or 8,000.

### Selection

| Arm | dlift lower bound > 0 | V1 | V3 | V4 [2.30, 6.90] | V5 | F | Selected |
|---|---|---|---|---|---|---|---|
| 6,000 | -0.0124: **fail** | 0.013: pass | 0 of 25: pass | 2.93: pass | pass | 0.045: pass | no |
| 8,000 | -0.0237: **fail** | 0.000: pass | 0 of 26: pass | 2.40: pass | pass | 0.083: pass | no |
| 12,000 | -0.0095: **fail** | 0.047 > 0.022 + 0.02: **fail** | 0 of 12: pass | 2.20: **fail** | pass | 0.061: pass | no |

**No winner. 4,000 stays** (the #1269 rule 4: nothing is retried with tuned
bars).

## Exploratory model: `qwen3.6:27b`, NOT RUN

The registered budget for its uncached work, stage 0 included, was ≤ 4.0 h.
Its stage 0 (522 s, table above) gave this forecast, from the registered
formula with #1557's timings (D = 2927 s, P = 446 s, t at 4,000 = 53.4 s):

| Work | Forecast |
|---|---|
| 6,000, two runs: 2 × (2927 × 27 × 93.6 / (41 × 53.4) + 446 × 3 × 93.6 / (4 × 53.4)) | 7,930 s |
| 8,000, two runs: 2 × (2927 × 22 × 83.8 / (41 × 53.4) + 446 × 2 × 83.8 / (4 × 53.4)) | 5,630 s |
| `medium-10` seed 2 at 4,000 | 446 s |
| stage 0, already spent | 522 s |
| **Total** | **14,528 s = 4.04 h > 4.0 h** |

The rule therefore did not run it.

- Its 4,000-char runs (seeds 1 and 2) were replayed from #1557's cache. Every
  call was a cache hit.
- No 6,000 or 8,000 extraction was made with it.
- Its stage-0 rows are the only exploratory data. They show that a 6,000 or
  8,000 window costs it 1.6 to 1.8 times its 4,000 median per call.

## What the numbers say, and what they do not

- **Larger windows produce fewer objects.** The final count falls from 4.60
  per meeting-run at 4,000 to 2.93, 2.40 and 2.20 at 6,000, 8,000 and 12,000.
  This is #454's one-object attractor at work: the count follows the number
  of calls (41 → 27 → 22 → 15 windows).
- **Coverage falls with the count.** Own soft coverage falls in every gold
  section, and judge coverage falls from 0.255 to 0.129. Precision does not
  rise to compensate: G1 is flat, at 0.89 to 0.94 for every arm.
- **Fragmentation is not the problem a larger window would fix.** On AMI gold,
  4,000 has the most redundant final emissions: 9 of 69 (0.130). That share
  falls with fewer boundaries, but the lost coverage outweighs it. On
  `medium-10`, #699's repeat count is 0 at 4,000 in all three runs.
- **`medium-10`** (the #699 fixture) reproduces #699's direction. Exact recall
  is 22 of 33 at 4,000, 19 of 33 at 8,000 (#699 had 0.80 → 0.69) and 12 of 33
  at 12,000.
- **The deterministic drops cost more at 8,000.** In the stage table, 8,000's
  meeting lift turns negative at the drops stage: 63 → 29 objects. That is
  one arm's evidence, not a measured mechanism.
- **The CIs are coarse.** They rest on 5 meetings, so only 8,000's negative
  delta and 12,000's judge-coverage loss exclude zero. The screen can say that
  no larger window helps `qwen3:8b`. It cannot rank 6,000 against 12,000.
- **Latency.** Per meeting-run, 6,000 and 8,000 were 11-14% faster than
  4,000, and 12,000 was 8% slower. None of this is gated.

## Wall-clock against the forecast

| Phase | Forecast | Actual |
|---|---|---|
| Stage 0, `qwen3:8b` (3 arms) | — | 483 s |
| 4,000: replay, then seed 3 and `medium-10` seeds 2 and 3 | 0.5 h | replay 0 s; new runs 2,070 s (0.58 h) |
| 6,000, n = 3 | 1.32 h (from stage 0) | 4,373 s (1.21 h) |
| 8,000, n = 3 | 1.44 h | 4,332 s (1.20 h) |
| 12,000, n = 3 | 1.10 h | 5,459 s (1.52 h; 38% over) |
| Judge (G1 plus coverage), 4 arms | — | 2,412 s (0.67 h) |
| Stage 0, `qwen3.6:27b` (2 arms) | — | 522 s |
| **Total model time** | about 3.2 h + 1.4 h before stage 0 | **19,691 s (5.47 h)**, 05:11 to 10:40 |

## Incidents and deviations

- **Participant-capture calls hit the cache across arms.** That call reads the
  whole source and is the same in every arm. So in the 6,000, 8,000 and
  12,000 runs, its seed-r call replayed the 4,000 arm's cached reply. It is
  the same call, so this is correct, but those arms' wall-clock excludes
  those calls.
- **The over-budget participant-capture runaways** are present in every
  arm. They are not caused by the window size; see the feasibility table.
- No other deviation from the registration.

## Reproduce

```sh
uv run python evals/concept_window_screen/run_window_screen.py stage0 --model qwen3:8b --arms 6000 8000 12000
uv run python evals/concept_window_screen/run_window_screen.py run --model qwen3:8b --arm 4000 --runs 1 2 --replay-only
uv run python evals/concept_window_screen/run_window_screen.py run --model qwen3:8b --arm 4000 --runs 1 2 3 --prose
uv run python evals/concept_window_screen/run_window_screen.py run --model qwen3:8b --arm 6000 --runs 1 2 3 --prose
uv run python evals/concept_window_screen/run_window_screen.py judge --model qwen3:8b --arm 6000 --runs 1 2 3
uv run python evals/concept_window_screen/run_window_screen.py score --name w8b --model qwen3:8b --arms 4000 6000 8000 12000 --runs 1 2 3 --prose-runs 1 2 3
uv run python evals/concept_window_screen/run_window_screen.py decide --name w8b
```

With the cache in place, every `run` replays without a model call.
