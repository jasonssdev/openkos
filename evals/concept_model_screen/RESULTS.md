# Results: choosing the concept-extraction model (#1557)

**Verdict: NO WINNER. `qwen3:8b` stays. Nothing is adopted.** Under the rules
in [`PREREGISTRATION.md`](PREREGISTRATION.md), which merged in #1567 before
any candidate run, round 1 vetoed three of the four candidates. The fourth,
`qwen3.6:27b`, failed the selection in round 2: its paired window-level lift
delta is -0.0079 [-0.0423, +0.0196], and its G1 precision delta has a lower
bound of -0.1739, below the -0.05 floor. With no winner, the confirmation on
the free pool did not run. No default (`DEFAULT_MODEL`, `DEFAULT_TASK_MODELS`,
`_CHUNK_TARGET`, any prompt) changes.

**Top two for #1558:** the registered rule names only **`qwen3:8b`**. The rule
admits the baseline plus every round-2 candidate that holds every veto and G1.
The only round-2 candidate was `qwen3.6:27b`, and it fails G1. No second model
qualifies.

## Run

- Dates: 2026-10-18 22:32 to 2026-10-19 03:14 (EST). The harness was at
  `ebbde16f`, the registration merge. Two harness fixes followed, and neither
  changes a generation:
  - `360a6631` records the dedup stage once (see "Incidents").
  - `67971eb3` names the shortened confirmation pool.
- Production `extract_concept_union` ran unpatched, with `num_ctx` 12288,
  `num_predict` 8192, `think` false and `seed = r`. Only the model tag varied.
  Model digests match the registration.
- Before stage 0, the screen waited for another agent's Ollama job (#1561) to
  finish, which happened at 22:32. Ollama was then used by this screen alone.
  `ollama ps` at that moment showed only that job's model, still resident.
- Raw generations, judge replies and embeddings are kept outside both
  repositories, in `~/Dev/Projects/openkos-eval-runs/concept-screen/`.

## Stage 0

**Abort test (`qwen3:8b`): an aborted HTTP call DOES stop server-side
generation.**

| Measurement | Seconds |
|---|---|
| T0, three tiny calls on a warm model | 0.137, 0.130, 0.131 |
| D, one long generation, not aborted | 16.84 |
| The same generation, aborted by the 5 s client timeout | 5.00 (`OllamaUnavailable`) |
| T1, a tiny call sent at once | 0.139 |
| T2, a second tiny call | 0.139 |

T1 is 0.139 s against a bound of 0.131 + 0.25 × (16.84 - 5) = 3.09 s. A zombie
generation would have held the queue for about 11.8 s, and nothing waited.
This answers #1562's question: a client-side timeout is a real speed lever.

| Model | Stage-0 window seconds (3 windows) | Median | Window timeout | Whole-source timeout | `ollama ps` size at `num_ctx` 12288 (with `bge-m3`, 664 MB) |
|---|---|---|---|---|---|
| `qwen3:8b` | 14.2, 36.5, 39.8 | 36.5 | 146 s | 438 s | 6.4 GB |
| `gemma4:26b-a4b` | 12.1, 14.0, 7.0 | 12.1 | 60 s | 180 s | 985 MB as reported (implausible for a 26B MoE; `ollama ps` likely misreports this architecture) |
| `qwen3:14b` | 42.6, 25.3, 26.2 | 26.2 | 105 s | 315 s | 10 GB |
| `qwen3.6:27b` | 50.9, 53.4, 87.4 | 53.4 | 213 s | 640 s | 17 GB (`qwen3:14b`, 10 GB, was still resident) |
| `qwen3.6:35b-a3b` | 7.2, 11.8, 7.1 | 7.2 | 60 s | 180 s | 22 GB |

Two `gpt-oss:20b` G1 calls took 5.60 s and 4.47 s.

## Validity checks (baseline round 1, before any candidate ran)

| Check | Result | Verdict |
|---|---|---|
| 1. Separation | baseline window lift +0.0306 [+0.0107, +0.0580]; needs lower bound > 0 and point ≥ 0.03 | **pass** (by 0.0006 on the point) |
| 2. Planted deletion | own-window sentences -0.0357 (n = 38), other sentences -0.0136 (n = 264); needs ≤ -0.03 and below the other | **pass** |
| 3. Matcher calibration | threshold 0.9125 reaches precision 0.963 (26 of 27) on 268 positive and 4260 negative pairs; recall 26 of 268 (0.097) | threshold found, nearly useless |

The embedding lift therefore stayed primary, and no fallback ran. Both checks
passed by a thin margin. Because check 3's recall is 0.097, embedding-matched
subject recall on prose barely differs from exact match.

## Round 1 (n = 1, pool DA plus the five prose fixtures)

"dlift" is the paired window-level delta of the primary metric, candidate
minus baseline. It is computed over 37 windows with gold in 5 meetings, using
a two-stage bootstrap with 10,000 resamples and seed 42. "V3" counts
wrong-language titles on the two Spanish fixtures.

| Model | window dlift [95% CI] | meeting dlift [95% CI] | window lift (mean) | meeting lift (mean) | failures (V1) | causes | objects / meeting-run (V4) | verbatim (V5) | es-fixture wrong-language titles (V3) | dropped by the language filter | median s / window call | s / meeting-run | total s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `qwen3:8b` | — | — | 0.0306 | 0.0045 | 1 of 41 (0.024) | {'unparseable': 1} | 5.40 (27 in 5) | 0 of 27 (0.000) | 0 of 16 (0.000) | 1 | 31.4 | 244.6 | 1223 |
| `qwen3.6:35b-a3b` | +0.0111 [-0.0258, +0.0393] | +0.0131 [-0.0092, +0.0355] | 0.0417 | 0.0176 | 0 of 41 (0.000) | — | 6.40 (32 in 5) | 0 of 32 (0.000) | 2 of 13 (0.154) | 1 | 8.5 | 99.4 | 497 |
| `gemma4:26b-a4b` | +0.0001 [-0.0263, +0.0274] | +0.0137 [-0.0032, +0.0291] | 0.0308 | 0.0182 | 0 of 41 (0.000) | — | 11.40 (57 in 5) | 0 of 57 (0.000) | 0 of 16 (0.000) | 0 | 15.3 | 154.3 | 771 |
| `qwen3:14b` | +0.0253 [-0.0192, +0.0653] | +0.0149 [+0.0031, +0.0268] | 0.0559 | 0.0194 | 2 of 41 (0.049) | {'timeout': 2} | 14.00 (70 in 5) | 0 of 70 (0.000) | 1 of 29 (0.034) | 0 | 28.0 | 341.3 | 1706 |
| `qwen3.6:27b` | -0.0068 [-0.0414, +0.0198] | +0.0214 [+0.0078, +0.0415] | 0.0238 | 0.0260 | 0 of 41 (0.000) | — | 5.60 (28 in 5) | 0 of 28 (0.000) | 0 of 15 (0.000) | 0 | 47.2 | 531.4 | 2657 |

| Model | title language en / es / neither | prose subject recall, exact | prose subject recall, embedding | whole-source prose failures | judge statuses | judged out | participant additions |
|---|---|---|---|---|---|---|---|
| `qwen3:8b` | 10 / 0 / 17 | 26 of 33 (0.788) | 26 of 33 (0.788) | 0 | {'ok': 5, 'failed': 0, 'empty': 0, 'skipped': 0} | 4 | 9 |
| `qwen3.6:35b-a3b` | 17 / 0 / 15 | 10 of 33 (0.303) | 11 of 33 (0.333) | 0 | {'ok': 5, 'failed': 0, 'empty': 0, 'skipped': 0} | 26 | 3 |
| `gemma4:26b-a4b` | 15 / 0 / 42 | 15 of 33 (0.455) | 15 of 33 (0.455) | 0 | {'ok': 5, 'failed': 0, 'empty': 0, 'skipped': 0} | 29 | 17 |
| `qwen3:14b` | 36 / 0 / 34 | 30 of 33 (0.909) | 30 of 33 (0.909) | 0 | {'ok': 5, 'failed': 0, 'empty': 0, 'skipped': 0} | 42 | 11 |
| `qwen3.6:27b` | 8 / 0 / 20 | 14 of 33 (0.424) | 17 of 33 (0.515) | 0 | {'ok': 5, 'failed': 0, 'empty': 0, 'skipped': 0} | 37 | 12 |

Own vs foreign coverage curve (final objects, meeting level, mean share of gold with best cosine >= tau):

| Model | 0.50 | 0.55 | 0.60 | 0.65 | 0.70 | 0.75 | 0.80 | 0.85 | 0.90 |
|---|---|---|---|---|---|---|---|---|---|
| `qwen3:8b` own | 0.602 | 0.455 | 0.277 | 0.100 | 0.050 | 0.000 | 0.000 | 0.000 | 0.000 |
| `qwen3:8b` foreign | 0.599 | 0.458 | 0.216 | 0.080 | 0.022 | 0.000 | 0.000 | 0.000 | 0.000 |
| `qwen3.6:35b-a3b` own | 0.798 | 0.612 | 0.353 | 0.163 | 0.059 | 0.000 | 0.000 | 0.000 | 0.000 |
| `qwen3.6:35b-a3b` foreign | 0.750 | 0.485 | 0.196 | 0.079 | 0.022 | 0.000 | 0.000 | 0.000 | 0.000 |
| `gemma4:26b-a4b` own | 0.816 | 0.567 | 0.303 | 0.166 | 0.083 | 0.000 | 0.000 | 0.000 | 0.000 |
| `gemma4:26b-a4b` foreign | 0.806 | 0.445 | 0.207 | 0.102 | 0.020 | 0.000 | 0.000 | 0.000 | 0.000 |
| `qwen3:14b` own | 0.865 | 0.726 | 0.426 | 0.218 | 0.046 | 0.000 | 0.000 | 0.000 | 0.000 |
| `qwen3:14b` foreign | 0.862 | 0.591 | 0.340 | 0.108 | 0.027 | 0.000 | 0.000 | 0.000 | 0.000 |
| `qwen3.6:27b` own | 0.769 | 0.520 | 0.301 | 0.194 | 0.028 | 0.011 | 0.000 | 0.000 | 0.000 |
| `qwen3.6:27b` foreign | 0.673 | 0.368 | 0.182 | 0.070 | 0.011 | 0.000 | 0.000 | 0.000 | 0.000 |

| Model | abstract | decisions | problems |
|---|---|---|---|
| `qwen3:8b` | 0.571 | 0.507 | 0.479 |
| `qwen3.6:35b-a3b` | 0.591 | 0.553 | 0.492 |
| `gemma4:26b-a4b` | 0.598 | 0.557 | 0.491 |
| `qwen3:14b` | 0.617 | 0.570 | 0.529 |
| `qwen3.6:27b` | 0.587 | 0.533 | 0.496 |


### Halving trace

| Candidate | Verdict | Reason |
|---|---|---|
| `qwen3.6:35b-a3b` | dropped | V3: 2 of 13 (0.154) wrong-language titles > 0.000 + 0.02 |
| `gemma4:26b-a4b` | dropped | V4: 11.40 objects per meeting-run, outside [2.70, 8.10] |
| `qwen3:14b` | dropped | V1: 2 of 41 (0.049) > 0.024 + 0.02 (two timeouts); V3: 1 of 29 (0.034); V4: 14.00 outside [2.70, 8.10] |
| `qwen3.6:27b` | **kept** (rank 1 by point dlift) | every veto holds; dlift upper bound +0.0198 ≥ 0 |

The rule keeps the top ⌈4/2⌉ = 2. Only one candidate survived the vetoes, so
one went forward. Volume (V4) is measured against a sparse baseline: 5.40
objects per meeting-run, or 27 in 5. `gemma4:26b-a4b` and `qwen3:14b` produced
2.1x and 2.6x that volume. Without the veto, `qwen3:14b` had the best point
dlift (+0.0253). Its CI, [-0.0192, +0.0653], also contains 0.

### Re-forecast and the shortening rule

Before round 1, the stage-0 forecast was 20.37 h:

| Part | Forecast |
|---|---|
| Round 1 | 2.86 h |
| Round 2 (the two slowest candidates, as registered) | 4.90 h |
| G1 | 0.58 h |
| Confirmation | 12.03 h |

After round 1 the forecast was redone from measured seconds per
window-equivalent: 1618 / 76 = 21.29 s for `qwen3:8b` and 3858 / 76 = 50.76 s
for `qwen3.6:27b`, the only survivor. The ladder was applied in order:

| Step | Forecast |
|---|---|
| As registered | 15.95 h |
| 1. Confirmation drops the IB meetings | 12.52 h |
| 2. Confirmation drops TS3012 | 10.27 h |
| 3. Confirmation at n = 2 | 8.91 h |
| 4. Round-2 prose at n = 1 | 8.11 h |
| 5. Round 2 at n = 2 | **6.86 h** |

All five steps applied. **Round 2 ran at n = 2, not 3.** V3 was read from the
prose run 1. The confirmation, had it run, would have used IS1002 and IS1005
at n = 2. Each ladder step was decided at the latest forecast before the
phase it affects.

## Round 2 (n = 2: runs 1 and 2, pool DA; prose run 1)

| Model | window dlift [95% CI] | meeting dlift [95% CI] | window lift (mean) | meeting lift (mean) | failures (V1) | causes | objects / meeting-run (V4) | verbatim (V5) | es-fixture wrong-language titles (V3) | dropped by the language filter | median s / window call | s / meeting-run | total s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `qwen3:8b` | — | — | 0.0319 | 0.0077 | 2 of 82 (0.024) | {'unparseable': 2} | 5.00 (50 in 10) | 0 of 50 (0.000) | 0 of 16 (0.000) | 1 | 33.4 | 292.0 | 2920 |
| `qwen3.6:27b` | -0.0079 [-0.0423, +0.0196] | +0.0160 [+0.0040, +0.0284] | 0.0239 | 0.0237 | 0 of 82 (0.000) | — | 6.40 (64 in 10) | 0 of 64 (0.000) | 0 of 15 (0.000) | 0 | 51.9 | 585.4 | 5854 |

| Model | title language en / es / neither | prose subject recall, exact | prose subject recall, embedding | whole-source prose failures | judge statuses | judged out | participant additions |
|---|---|---|---|---|---|---|---|
| `qwen3:8b` | 21 / 0 / 29 | 26 of 33 (0.788) | 26 of 33 (0.788) | 0 | {'ok': 10, 'failed': 0, 'empty': 0, 'skipped': 0} | 13 | 9 |
| `qwen3.6:27b` | 22 / 0 / 42 | 14 of 33 (0.424) | 17 of 33 (0.515) | 0 | {'ok': 10, 'failed': 0, 'empty': 0, 'skipped': 0} | 85 | 33 |

| Model | G1 precision (supported of labelled) | partial | unsupported | unparsed windows | dprecision [95% CI] | judge coverage (mean) |
|---|---|---|---|---|---|---|
| `qwen3:8b` | 38 of 41 (0.927) | 1 | 2 | 0 | — | 0.265 |
| `qwen3.6:27b` | 28 of 31 (0.903) | 2 | 1 | 0 | -0.0236 [-0.1739, +0.1135] | 0.230 |

Own vs foreign coverage curve (final objects, meeting level, mean share of gold with best cosine >= tau):

| Model | 0.50 | 0.55 | 0.60 | 0.65 | 0.70 | 0.75 | 0.80 | 0.85 | 0.90 |
|---|---|---|---|---|---|---|---|---|---|
| `qwen3:8b` own | 0.636 | 0.441 | 0.237 | 0.111 | 0.035 | 0.005 | 0.000 | 0.000 | 0.000 |
| `qwen3:8b` foreign | 0.618 | 0.427 | 0.177 | 0.076 | 0.027 | 0.011 | 0.006 | 0.000 | 0.000 |
| `qwen3.6:27b` own | 0.741 | 0.553 | 0.302 | 0.163 | 0.029 | 0.011 | 0.000 | 0.000 | 0.000 |
| `qwen3.6:27b` foreign | 0.665 | 0.427 | 0.207 | 0.076 | 0.021 | 0.000 | 0.000 | 0.000 | 0.000 |

| Model | abstract | decisions | problems |
|---|---|---|---|
| `qwen3:8b` | 0.567 | 0.518 | 0.469 |
| `qwen3.6:27b` | 0.583 | 0.538 | 0.507 |


### Selection

| Candidate | dlift lower bound > 0 | V1 | V3 | V4 | V5 | G1 lower bound ≥ -0.05 | Selected |
|---|---|---|---|---|---|---|---|
| `qwen3.6:27b` | -0.0423: **fail** | 0 of 82: pass | 0 of 15: pass | 6.40 in [2.50, 7.50]: pass | 0 of 64: pass | -0.1739: **fail** | no |

**No winner. The baseline stays** (the #1269 rule 4: nothing is retried with
tuned bars). The confirmation on pool F was not run.

What the numbers say, and what they do not:

- **The primary does not separate `qwen3.6:27b` from `qwen3:8b`.**
  - Window-level delta: -0.0079 [-0.0423, +0.0196].
  - The meeting-level secondary favours `qwen3.6:27b`: +0.0160
    [+0.0040, +0.0284]. That is reported only, not decided on.
  - The two levels disagree because `qwen3.6:27b`'s whole-source additions
    lift the meeting score but belong to no window:
    - participant capture added 33 objects against 9;
    - the selector judge cut 85 candidates against 13.
- **G1 is underpowered at this volume.** Precision was 38 of 41 (0.927) for
  `qwen3:8b` and 28 of 31 (0.903) for `qwen3.6:27b`. A CI of
  [-0.1739, +0.1135] on 72 labelled objects cannot clear a -0.05 floor even at
  a point delta of -0.0236. The registered bar did not anticipate how few
  objects the pipeline keeps.
- **Judge coverage (reported only)** was 0.265 for `qwen3:8b` and 0.230 for
  `qwen3.6:27b`, the mean over 10 meeting-runs each.
- **Run-to-run swing.** `qwen3:8b` produced 2 to 12 objects per meeting-run.
  Its round-1 window lift was 0.0306 and its 2-run lift 0.0319.

## Top two for #1558

The registered rule takes the baseline (delta 0), plus every round-2 candidate
holding every veto and G1, ranked by point delta. It gives **`qwen3:8b` only**.
`qwen3.6:27b` fails G1 and has a negative point delta. **#1558 has no
registered second model.** Its own registration has to decide whether to run
`qwen3:8b` alone, or to name a second model on other grounds and label that
choice exploratory.

## Per-stage loss (#1559)

This is a rescore of the recorded stages of the real pipeline, with no extra
generation. It covers pool DA, runs 1 and 2: 10 meeting-runs and 82
window-runs per model.

- "own" and "foreign" are meeting-level soft coverage of all gold.
- "window lift" is the primary metric applied to each stage's objects.
- The row-to-row change in window lift is that stage's loss.

The 24-candidate ceiling and the 20-object backstop never bound: there were 0
pre-judge drops and 0 backstop discards for both models. No stage's re-ask
added anything (0 re-ask additions). The "pre_ceiling" rows add only the
participant capture.

#### `qwen3:8b` (r2)

| Stage | objects (sum over meeting-runs) | own soft cov | foreign soft cov | meeting lift | own gold covered at >= 0.70 | foreign gold covered at >= 0.70 | window lift | change in window lift |
|---|---|---|---|---|---|---|---|---|
| raw | 76 | 0.5608 | 0.5482 | 0.0126 | 8 of 194 | 8 of 208 | 0.0464 | — |
| dedup | 75 | 0.5605 | 0.5477 | 0.0128 | 8 of 194 | 8 of 208 | 0.0464 | +0.0000 |
| drops | 54 | 0.5453 | 0.5355 | 0.0097 | 6 of 194 | 6 of 208 | 0.0357 | -0.0107 |
| pre_ceiling | 63 | 0.5478 | 0.5376 | 0.0102 | 6 of 194 | 6 of 208 | 0.0357 | +0.0000 |
| ceiling | 63 | 0.5478 | 0.5376 | 0.0102 | 6 of 194 | 6 of 208 | 0.0357 | +0.0000 |
| judge | 50 | 0.5382 | 0.5305 | 0.0077 | 5 of 194 | 5 of 208 | 0.0319 | -0.0038 |
| final | 50 | 0.5382 | 0.5305 | 0.0077 | 5 of 194 | 5 of 208 | 0.0319 | +0.0000 |

#### `qwen3.6:27b` (r2)

| Stage | objects (sum over meeting-runs) | own soft cov | foreign soft cov | meeting lift | own gold covered at >= 0.70 | foreign gold covered at >= 0.70 | window lift | change in window lift |
|---|---|---|---|---|---|---|---|---|
| raw | 138 | 0.5905 | 0.5670 | 0.0235 | 12 of 194 | 4 of 208 | 0.0716 | — |
| dedup | 137 | 0.5905 | 0.5669 | 0.0235 | 12 of 194 | 4 of 208 | 0.0710 | -0.0006 |
| drops | 116 | 0.5823 | 0.5604 | 0.0219 | 12 of 194 | 4 of 208 | 0.0652 | -0.0058 |
| pre_ceiling | 149 | 0.5950 | 0.5682 | 0.0268 | 12 of 194 | 5 of 208 | 0.0652 | +0.0000 |
| ceiling | 149 | 0.5950 | 0.5682 | 0.0268 | 12 of 194 | 5 of 208 | 0.0652 | +0.0000 |
| judge | 64 | 0.5568 | 0.5331 | 0.0237 | 6 of 194 | 4 of 208 | 0.0239 | -0.0413 |
| final | 64 | 0.5568 | 0.5331 | 0.0237 | 6 of 194 | 4 of 208 | 0.0239 | +0.0000 |

**Window lift by stage, round 1 (n = 1), every model** (object counts in brackets):

| Model | raw | dedup | drops | +additions | ceiling | judge | final (backstop) |
|---|---|---|---|---|---|---|---|
| `qwen3:8b` | 0.0478 (32) | 0.0478 (31) | 0.0390 (22) | 0.0390 (31) | 0.0390 (31) | 0.0306 (27) | 0.0306 (27) |
| `qwen3.6:35b-a3b` | 0.0639 (76) | 0.0642 (76) | 0.0531 (55) | 0.0531 (58) | 0.0531 (58) | 0.0417 (32) | 0.0417 (32) |
| `gemma4:26b-a4b` | 0.0591 (91) | 0.0563 (89) | 0.0405 (69) | 0.0405 (86) | 0.0405 (86) | 0.0308 (57) | 0.0308 (57) |
| `qwen3:14b` | 0.0769 (164) | 0.0774 (161) | 0.0737 (152) | 0.0737 (163) | 0.0672 (112) | 0.0559 (70) | 0.0559 (70) |
| `qwen3.6:27b` | 0.0630 (64) | 0.0619 (63) | 0.0636 (53) | 0.0636 (65) | 0.0636 (65) | 0.0238 (28) | 0.0238 (28) |

**Headline.**

- **Every candidate's raw window replies cover the gold better than
  `qwen3:8b`'s.** Raw window lift is 0.0591 to 0.0769 for the candidates,
  against 0.0478. Two pipeline stages then remove most of that advantage:
  - **the deterministic drops** (framing objects and source-title twins; the
    wrong-language filter dropped 0 titles on these English meetings):
    -0.0088 on `qwen3:8b`, -0.0111 on `qwen3.6:35b-a3b`, -0.0158 on
    `gemma4:26b-a4b`;
  - **the selector judge**:
    - -0.0084 on `qwen3:8b`;
    - -0.0398 on `qwen3.6:27b` (0.0636 → 0.0238 in round 1, and 0.0652 →
      0.0239 at n = 2), where it cut 149 candidates to 64 at n = 2.
- **Window lift is the primary.** It counts objects attributed to a window.
  The participant-capture additions belong to no window, so they cannot lift
  it; they move only the meeting-level lift (0.0219 → 0.0268 for
  `qwen3.6:27b`).
- **The one-object attractor (L2) is visible.** `qwen3:8b` returned 76 raw
  objects from 82 window calls; `qwen3.6:27b` returned 138.
- **The 24-candidate ceiling** cut only on `qwen3:14b` in round 1 (163 → 112
  over 5 meeting-runs; window lift -0.0065).
- The judge loss and the drop loss are material. Each is a candidate for its
  own fix issue (#1559's done-when). On this evidence, the model choice is
  confounded with the selector judge: a stronger extractor is judged by
  itself, and on `qwen3.6:27b` that judge keeps less than half of what the
  windows found.

## Wall-clock against the forecast

| Phase | Forecast | Actual |
|---|---|---|
| Stage 0 | 15-25 min (design) | 530 s (8.8 min) |
| Round 1 (5 models, DA plus prose, validity included) | 2.86 h (stage 0) | 9932 s (2.76 h) |
| Round 2 (2 models, 1 extra run, DA only) | 1.12 h (post-round-1, after the ladder) | 4898 s (1.36 h) |
| G1 plus judge coverage | 0.26 h | 1329 s (0.37 h) |
| Confirmation | 2.73 h | not run (no winner) |
| **Total model time** | 6.86 h (with confirmation) | 16689 s (4.64 h), 22:32 to 03:14 |

The design's own forecast was 2.5-4 h for selection plus 1-1.5 h for
confirmation, with a 20-30 s per-window assumption. Stage 0 measured 36.5 s
for `qwen3:8b`'s median window, and the full pipeline adds about 20% for each
meeting's participant capture and judge. Round 2 overran its forecast by 21%:
`qwen3:8b` spent 1697 s on DA against 1223 s in round 1, a run-to-run
difference with no timeout and no failure.

## Incidents and deviations

- **Stage-capture bug, fixed mid-run.** The re-ask and the participant capture
  call `_dedup_merged` again on the merged list plus their additions. The
  first harness version therefore recorded the last call as the "dedup"
  stage. Fixed in `360a6631`, with a self-test that fails on the old
  behaviour.
  - `qwen3:8b`'s round-1 records were made before the fix. They were moved
    aside and replayed from the call cache: 100% cache hits, 6 s, identical
    object counts.
  - Every candidate run started after the fix.
  - No scored number depends on the old capture.
- **Round 2 at n = 2**, from the registered shortening rule (above), not 3.
- **G1 and V-vetoes were computed on 2 runs.** V3 used prose run 1 only
  (shortening step 4).
- **Thesis repository.** While locating its transcripts, one `git status` was
  run in the read-only thesis checkout. It printed nothing and changed no
  file, but it can refresh `.git/index` stat data. Nothing else touched that
  repository; its transcripts and the AMI zip were copied out (the zip's
  sha256 matches the pin).

## Reproduce

```sh
uv run python evals/concept_model_screen/run_concept_screen.py prepare
uv run python evals/concept_model_screen/run_concept_screen.py stage0
uv run python evals/concept_model_screen/run_concept_screen.py run --models qwen3:8b --pool DA --runs 1 --prose
uv run python evals/concept_model_screen/run_concept_screen.py score --name r2 --models qwen3:8b qwen3.6:27b --pool DA --runs 1 2 --prose-runs 1 --g1
uv run python evals/concept_model_screen/run_concept_screen.py decide --name r2 --round 2
```

With the cache in place, every `run` replays without a model call.
