# Results: gold content lost at each extraction stage (#1559)

**Verdict: two loss points on the default path carry almost all of the
measured loss, and the rest are small, absent, or not reachable from stored
data.** From the raw window replies to the final set, the pooled window lift
falls by -0.0256 [-0.0514, -0.0068] (round 1, five models, 25 meeting-runs).
The selector judge (L10) is -0.0161 of that and the deterministic drops (L7)
are -0.0075. Dedup (L6), the judge ceiling (L8) and the backstop (L11) are
under -0.0015 together. The legacy cap (L12) is large when it applies, but it
applies only with `union_judge` off, which is not the default.

The rules are in [`STAGE_LOSS_PREREGISTRATION.md`](STAGE_LOSS_PREREGISTRATION.md),
committed at `0e0c03f7` before any new figure was computed. Nothing was
generated, and no production code or default changes.

## Run

- The harness is `stage_loss.py` (`score`, `--self-test`). It was committed at
  `3f4f7bd6` and `4020d35c`.
- **No model call.** Every command ran with `OLLAMA_HOST=http://127.0.0.1:1`,
  and the embedding function raises on a cache miss. There were **0 cache
  misses** in either cache (#1557's and #1558's), so nothing was deferred for
  want of an embedding.
- **Reconciliation passed before any new number was read.** The recomputed
  stage lifts match the published #1559 table to four decimals on all five
  models, and the recomputed L7 loss equals #1572's.
- Mutation check on the core computations: 13 mutations of the stage-order
  chain, the cap, the covered-sentence counter, the contrast sign, the
  materiality and L1 rules, the imputation, the context-window count, the
  reconciliation rounding and the straddle test. All 13 are caught by the
  self-test, after the first run left 4 surviving and their cases were added.
  `__pycache__` was purged between runs.

## The table

"Measured" is a loss with an interval. "Exposure" means the data show whether
the loss point is reached but cannot isolate its cost. Losses are pooled over
the five models (each window's delta is averaged over the models first, so the
interval describes a typical candidate). **New** marks a number this study
produced; **cited** marks one reused from an earlier study.

| # | Loss point | Result | Kind | Source |
|---|---|---|---|---|
| L1 | Window boundaries | Contrast not detected at any window size (see below). 36 of 90 linked gold sentences straddle a boundary at 4,000. | measured, low power | **new**; #1558 for the meeting-level arms (**cited**) |
| L2 | One-object attractor | 92 of 190 answered window calls (48%) returned exactly 1 object. Loss itself: **not measurable**, needs a counterfactual prompt. | exposure | **new**; #1558 mechanism (**cited**) |
| L3 | Skipped windows | 3 of 185 gold windows failed (1.6%), holding 10 of 450 gold sentences. Imputed lift at stake 0.0009. Not material. | measured | **new** |
| L4 | Unchunked runaway on 12-18 K prose | **Not measurable from stored data**; needs unchunked prose runs in the band, with gold. | not measurable | **new** (exposure only) |
| L5 | `num_ctx` binds before `num_predict` | 0 of 207 window calls exceeded `num_ctx`. Quality loss: **not measurable**, only runaways reach it. | exposure | **new**; #1558 (**cited**) |
| L6 | Dedup keeps the first | -0.0006 [-0.0028, +0.0003]; 1 of 450 gold sentences lost, 1 gained. Not material. | measured | **new** interval; per rule #1572 (**cited**) |
| L7 | Deterministic drops | -0.0075 [-0.0270, +0.0059]; 19 of 450 gold sentences lost. **Suggestive.** | measured | **new** pooled interval; per rule #1572 (**cited**) |
| L8 | Judge ceiling of 24 | -0.0013 [-0.0041, +0.0007]; 6 of 450 lost. Not material pooled; binds only on `qwen3:14b`. | measured | **new** interval |
| L9 | Judge reads an excerpt | 20 bounded judge prompts against 26 selector-judge calls. Effect on selection: **not measurable**, needs judge calls on the full source. | exposure | **new** |
| L10 | Judge selection | -0.0161 [-0.0276, -0.0081]; 38 of 450 lost; 5 of 5 models at the bar. **Material.** | measured | **new** pooled interval; coupling #1571 and precision #1579 (**cited**) |
| L11 | Backstop of 20 | 0.0000; 0 objects cut. Not material. | measured | **new** interval |
| L12 | Legacy cap of 6 | -0.0164 [-0.0268, -0.0058]; 216 objects cut. **Material, legacy path only.** | measured (replay) | **new** |
| L13 | Decision-pass cap of 25 | **Not measurable from stored data**; the decision pass was never run. | not measurable | **new** |
| L14 | Silent Ollama truncation | 0 of 207 window calls near `num_ctx`; largest window prompt 3,131 tokens. Quality loss: **not measurable**. | exposure | **new**; #866 (**cited**) |

Not a loss point: the additions between the drops and the ceiling
(participant capture, re-ask) add 52 objects in round 1. None carries a
window, so window lift cannot credit them, and the stage reads +0.0000 on
every model. The metric is blind to them rather than showing they add nothing.

### Stage losses by model (window lift, round 1, n = 1)

Each cell is the later stage minus the earlier one, with the paired two-stage
bootstrap interval (10,000 resamples, seed 42; 5 meetings, 37 gold windows).

| Model | L6 dedup | L7 drops | L8 ceiling | L10 judge | L11 backstop | raw to final |
|---|---|---|---|---|---|---|
| `qwen3:8b` | +0.0000 [+0.0000, +0.0000] | -0.0089 [-0.0393, +0.0115] | +0.0000 [+0.0000, +0.0000] | -0.0083 [-0.0241, +0.0000] | +0.0000 [+0.0000, +0.0000] | -0.0172 [-0.0466, +0.0063] |
| `qwen3.6:35b-a3b` | +0.0003 [+0.0000, +0.0012] | -0.0110 [-0.0425, +0.0103] | +0.0000 [+0.0000, +0.0000] | -0.0114 [-0.0248, +0.0013] | +0.0000 [+0.0000, +0.0000] | -0.0222 [-0.0530, +0.0026] |
| `gemma4:26b-a4b` | -0.0028 [-0.0100, +0.0000] | -0.0157 [-0.0472, +0.0044] | +0.0000 [+0.0000, +0.0000] | -0.0098 [-0.0273, +0.0027] | +0.0000 [+0.0000, +0.0000] | -0.0283 [-0.0554, -0.0069] |
| `qwen3:14b` | +0.0005 [+0.0000, +0.0012] | -0.0036 [-0.0106, +0.0012] | -0.0066 [-0.0204, +0.0033] | -0.0113 [-0.0448, +0.0067] | +0.0000 [+0.0000, +0.0000] | -0.0210 [-0.0548, +0.0019] |
| `qwen3.6:27b` | -0.0012 [-0.0047, +0.0000] | +0.0017 [-0.0118, +0.0126] | +0.0000 [+0.0000, +0.0000] | -0.0397 [-0.0793, -0.0119] | +0.0000 [+0.0000, +0.0000] | -0.0392 [-0.0828, -0.0087] |
| **pooled** | -0.0006 [-0.0028, +0.0003] | -0.0075 [-0.0270, +0.0059] | -0.0013 [-0.0041, +0.0007] | **-0.0161 [-0.0276, -0.0081]** | +0.0000 [+0.0000, +0.0000] | -0.0256 [-0.0514, -0.0068] |

Models with a point at or below the -0.005 bar: L6 0 of 5, L7 3 of 5, L8 1 of
5, L10 5 of 5, L11 0 of 5.

Stage lifts, which reproduce the published #1559 table:

| Model | raw | dedup | drops | + additions | ceiling | judge | final |
|---|---|---|---|---|---|---|---|
| `qwen3:8b` | 0.0478 | 0.0478 | 0.0390 | 0.0390 | 0.0390 | 0.0306 | 0.0306 |
| `qwen3.6:35b-a3b` | 0.0639 | 0.0642 | 0.0531 | 0.0531 | 0.0531 | 0.0417 | 0.0417 |
| `gemma4:26b-a4b` | 0.0591 | 0.0563 | 0.0405 | 0.0405 | 0.0405 | 0.0308 | 0.0308 |
| `qwen3:14b` | 0.0769 | 0.0774 | 0.0737 | 0.0737 | 0.0672 | 0.0559 | 0.0559 |
| `qwen3.6:27b` | 0.0630 | 0.0619 | 0.0636 | 0.0636 | 0.0636 | 0.0238 | 0.0238 |

### In gold sentences

A windowed gold sentence is covered when its best cosine against its own
window's objects is at least 0.60. Pooled over the five models, there are 450
windowed gold-sentence cases (90 per model). Reported only.

| Loss point | covered before and not after | the reverse |
|---|---|---|
| L6 dedup | 1 of 450 | 1 of 450 |
| L7 drops | 19 of 450 | 0 of 450 |
| L8 ceiling | 6 of 450 | 0 of 450 |
| L10 judge | 38 of 450 | 0 of 450 |
| L11 backstop | 0 of 450 | 0 of 450 |

By model, lost at L7 and L10: `qwen3:8b` 5 and 2, `qwen3.6:35b-a3b` 6 and 6,
`gemma4:26b-a4b` 2 and 6, `qwen3:14b` 2 and 9, `qwen3.6:27b` 4 and 15.

## What dominates

Ordered by pooled point loss, among the points that are material or
suggestive (registered rule 5):

1. **L12, the legacy cap: -0.0164, material.** It applies only when
   `union_judge` is off. On the default path it does not exist, so it is not a
   production loss today. It would be the largest if that path were taken.
2. **L10, the selector judge: -0.0161, material.** On the default path it is
   the largest: 63% of the pooled raw-to-final loss (the stage losses sum to
   -0.0255, against -0.0256 measured end to end, a rounding difference). All
   five models are at the bar. `qwen3.6:27b` alone loses -0.0397.
3. **L7, the deterministic drops: -0.0075, suggestive.** 29% of the pooled
   loss. The interval holds 0, and 3 of 5 models are at the bar. #1572 showed
   that the framing drop is the whole of it and that most of the cost is a
   window whose only object was the meeting container (the one-object
   attractor, L2).

L3 (0.0009 imputed), L6, L8 and L11 are not material. L8 binds only on
`qwen3:14b` (-0.0066, 51 objects cut in #1572's count; here 6 of its 90 gold
sentences).

The earlier summary in the issue called L7 "material" on the strength of
three point estimates. With the interval, it is **suggestive**: no model's L7
interval excludes 0.

## L1, window boundaries: not detected, and low power

36 of 90 gold sentences with a linked dialogue act straddle a 4,000-char
boundary. At 6,000 that is 26 of 90, at 8,000 23 of 90, at 12,000 13 of 90.

For each coarser arm, the sentences that straddle at 4,000 and are contained
at the arm (R) are compared with those contained at both (C), as the change in
their best cosine to the `drops`-stage objects of any window. C nets out the
general effect of fewer objects. A positive contrast means removing the
boundary helped.

| Arm | resolved R | control C | other | contrast [95% CI] | verdict |
|---|---|---|---|---|---|
| 6,000 | 11 of 90 | 53 of 90 | 26 of 90 | +0.0203 [-0.0419, +0.1025] | not detected |
| 8,000 | 13 of 90 | 54 of 90 | 23 of 90 | -0.0131 [-0.1131, +0.0835] | not detected |
| 12,000 | 24 of 90 | 53 of 90 | 13 of 90 | +0.0498 [-0.0837, +0.1479] | not detected |

Overall: **not detected**. The intervals are about ±0.1 cosine, wide next to
any plausible effect, so this does not show boundaries cost nothing. It shows
that, on 5 meetings, the contrast cannot see a boundary effect, which agrees
with #1558's meeting-level result: no larger window improved lift (6,000
-0.0044 [-0.0124, +0.0036], 8,000 -0.0143 [-0.0237, -0.0064], 12,000 -0.0036
[-0.0095, +0.0039]), and every larger window produced fewer objects. In every
arm, both groups lose coverage against 4,000 (R: 0.567 to 0.535, 0.590 to
0.445, 0.567 to 0.423), so the general effect of a coarser window swamps any
boundary effect.

## L2, the one-object attractor: reached often, cost not isolated

- 92 of 190 answered window calls (48%) returned exactly one object. For
  `qwen3:8b` it is 32 of 32 answered windows (the other 9 of its 41 were empty or unparseable); for
  `qwen3.6:35b-a3b` 19 of 40, `gemma4:26b-a4b` 14 of 40, `qwen3.6:27b` 25 of
  40, and `qwen3:14b` 2 of 38.
- 31 windows hold at least 3 gold sentences and returned one object. They hold
  131 gold sentences, of which 114 (87%) are below the 0.60 cover at the raw
  stage. In windows with at least 3 gold sentences and more than one object,
  129 of 184 (70%) are below it, and over all windowed gold 324 of 450 (72%).
  The gap is 17 points, but windows are not randomized to one object or many,
  so it is an exposure, not a loss.
- The cost of the attractor needs a counterfactual (the same windows with a
  prompt that asks for several objects), which is a model call. #1558's
  window-size arms show the mechanism: 41, 27, 22 and 15 windows gave 4.60,
  2.93, 2.40 and 2.20 objects per meeting-run.

## L3, skipped windows: small, and the issue's "0" was wrong

`qwen3:14b` skipped 2 windows (`skipped_chunks`, both timeouts), and
`qwen3:8b` had 1 unparseable gold window. Pooled, **3 of 185 gold windows
(1.6%)** failed, holding 10 of 450 gold sentences. The failed windows score
lift 0 at every stage. Imputing the average lift of the other windows gives
0.0009 pooled (0.0042 on `qwen3:14b`, 0.0011 on `qwen3:8b`, 0 on the others).
That is an imputation, not a measurement, and it is under the 2% registered
bar, so L3 is **not material**. The issue's loss table said "L3, skipped
windows: 0"; that holds for four models and not for `qwen3:14b`.

In the window screen (`qwen3:8b`, n = 3), failed windows were 3 of 123 at
4,000, 3 of 81 at 6,000, 0 of 66 at 8,000 and 2 of 45 at 12,000 (cited from
#1558).

## L12, the legacy cap of 6: replay

Replaying "keep the first 6 of the `drops` stage" (production's legacy path,
`union_judge` off, no participant capture, no judge) against the full stage:
-0.0164 [-0.0268, -0.0058] pooled, 216 objects cut. By model:

| Model | L12 loss | objects cut |
|---|---|---|
| `qwen3:8b` | -0.0010 [-0.0037, +0.0000] | 2 |
| `qwen3.6:35b-a3b` | -0.0199 [-0.0380, -0.0034] | 28 |
| `gemma4:26b-a4b` | -0.0012 [-0.0162, +0.0108] | 39 |
| `qwen3:14b` | -0.0399 [-0.0674, -0.0158] | 122 |
| `qwen3.6:27b` | -0.0197 [-0.0393, -0.0032] | 25 |

The cap keeps the first window's objects, so it empties the later windows. It
is material, and it is the registered "material" label, but the path it
belongs to is off by default, and the re-ask additions production would add
before the cap are not replayed. It is not proposed as a fix issue here.

## Exposure rows

- **L5 and L14.** Window calls in round 1: 207, largest prompt 3,131 tokens,
  0 over `num_ctx`, 0 ended by `length`. The other call kinds: participant
  capture 25 calls (largest 6,752), selector judge 26 (largest 7,991), none
  over. In the window screen, window calls were 123, 81, 66 and 48 at the four
  arms, none over `num_ctx` (largest 2,973, 3,515, 4,099 and 5,119 tokens).
  The 12,000 arm ran 4 window calls to `length` and its `num_ctx` was 16384.
  Participant-capture calls went over `num_ctx` in 2 of 15 runs at 4,000, 2 of
  15 at 6,000 and 1 of 15 at 8,000, in line with #1558. A quality loss from
  either point needs a runaway, and the stored runs show none in a window
  call at 4,000 to 8,000.
- **L9.** 20 bounded `judge` prompts against 26 selector-judge calls in round
  1: most judge calls read an excerpt, so the loss point is reached. The
  effect on selection needs the judge run on the full source against the
  excerpt, which is a model call.

## Not measurable from stored data

| # | Why | What it needs |
|---|---|---|
| L4 | The five stored prose runs are the only candidates. 0 of 5 failed, and the one source in the 12-18 K band (`medium-10-reunion-plataforma`, 12,948 chars) took the chunked path, so no unchunked source in the band was run. Prose also has no window-level gold. | Unchunked runs on prose between 12,000 and 18,000 chars, with gold that supports the coverage-lift metric. Model calls. |
| L13 | The screen set `transcript_decisions=False` (`screen_pipeline.py`), so no decision-pass output was stored. | The decision pass run over the pool, then the cap-25 replay. Model calls. |
| L9 effect | See above. | The selector judge on the full source for the same candidates. Model calls. |
| L2 cost | See above. | A prompt arm that removes the attractor. Model calls. |
| L5, L14 quality | Only runaways reach the limits. | A forced-runaway study. Model calls. |

## Reuse by citation

- **L7, per rule:** #1572 (`DROP_ABLATION_RESULTS.md`): the framing drop is
  the whole of it, sound for its target class, one false positive filed as
  #1575.
- **L10, coupling and precision:** #1571 (`JUDGE_STUDY_RESULTS.md`): a fixed
  judge still loses lift, and the self-judging coupling is significant only on
  `qwen3.6:27b` (+0.0146 [+0.0018, +0.0369]). #1579 step 1
  (`CUT_PRECISION_RESULTS.md`): the cut is about as source-supported as the
  kept set, and the registered label is mixed or inconclusive.
- **Window size, feasibility, latency:** #1558 (`evals/concept_window_screen/RESULTS.md`).

## Secondary: runs 1 and 2 (reported only)

| Model | L6 | L7 | L8 | L10 | L11 | L12 |
|---|---|---|---|---|---|---|
| `qwen3:8b` | +0.0000 [+0.0000, +0.0000] | -0.0107 [-0.0401, +0.0077] | +0.0000 [+0.0000, +0.0000] | -0.0038 [-0.0119, +0.0024] | +0.0000 [+0.0000, +0.0000] | +0.0024 [-0.0002, +0.0076] |
| `qwen3.6:27b` | -0.0006 [-0.0023, +0.0000] | -0.0058 [-0.0204, +0.0041] | +0.0000 [+0.0000, +0.0000] | -0.0413 [-0.0761, -0.0154] | +0.0000 [+0.0000, +0.0000] | -0.0255 [-0.0474, -0.0061] |

The secondary agrees in direction with the primary, except `qwen3:8b`'s L12,
which is +0.0024 at n = 2. L10 on `qwen3.6:27b` excludes 0 again.

## Can #1559 close?

By the registered rule 7, yes. Every loss point is measured, an exposure, or
named as not measurable with its requirement. Of the material and suggestive
ones, L10 and L7 already have their fix issues (#1571, then #1579; #1572,
with #1575), and L12 belongs to a non-default path. Five requirements above
need model calls; they are follow-ups, not part of this issue.

## Deviations and caveats

- **The harness was committed after its first scoring run.** The first run
  was made before commit `3f4f7bd6`. It had one error and lacked two
  descriptive additions, and the figures above are from the re-run on the
  final code:
  - `skipped_chunks` is 1-based in production ("chunk i/N"), and the first
    run read it as 0-based. That put `qwen3:14b`'s failed gold windows at 3
    instead of 2 and the pooled share at 2.2% (4 of 185), over the 2% bar.
    The corrected count is 3 of 185 (1.6%). This was found when the numbers
    did not match the recorded windows, and the self-test now pins it.
  - The L2 comparison columns (`dense_multi`, all windowed gold) and the L3
    imputed lift were added after the first run. They are descriptive and no
    rule reads them. No stage, L12 or L1 figure changed between runs.
- **The window-lift metric is blind to windowless objects.** Participant
  capture and re-ask objects carry no window, so L-additions read zero. The
  meeting-level view of #1558 does credit them.
- **Pooling is over five models that share 5 meetings.** The pooled interval
  describes a typical candidate on these meetings, not an independent sample;
  5 meetings is the floor of the cluster bootstrap, and all intervals are
  wide.
- **L1 is a contrast of non-randomized groups** at n = 11 to 24 resolved
  sentences. Read it as a ruling-out of large effects only.
- **Titles and sentences are AMI-derived** and stay in the runs directory;
  only counts are published here. The score file is
  `~/Dev/Projects/openkos-eval-runs/concept-screen/scores/stage_loss.json`.

## Reproduce

```sh
OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/concept_model_screen/stage_loss.py --self-test
OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/concept_model_screen/stage_loss.py score
```
