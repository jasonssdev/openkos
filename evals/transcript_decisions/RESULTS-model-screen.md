# Results: screening generation models for the transcript decision pass

**Verdict: `qwen3.6:27b` advances; NOTHING ADOPTED.** Under the rule fixed in
[`PREREGISTRATION-model-screen.md`](PREREGISTRATION-model-screen.md) (merged in
#1554 before any screening run), two candidates cleared every condition
(`qwen3.6:27b` and `qwen3.6:35b-a3b`), and `qwen3.6:27b` advanced on the larger
mean F1 gain. `qwen3:14b` missed the ES2015 margin by 0.002. **The confirmatory n = 15 run
confirms `qwen3.6:27b`** against a fresh n = 15 of `qwen3:8b`: all four
conditions hold on both series (F1 at 0.60 +0.181 on ES2015, +0.172 on TS3009).
No default (model, key, prompt or cap) changes because of this screen.

## Run

- Date: 2026-10-18. Harness at `28ed7bed` (the registration merge), clean tree.
- Decision-pass prompt hash `6d3962b0dd75650c`, generation ceiling 8192, context
  window 12288, one retry per failed window; only the model tag varied.
- AMI ES2015 a-d (27 gold `decision` rows) and TS3009 a-d (26), pass arm,
  5 runs per model per series: 20 meeting-runs each, all 160 completed, exit 0.
- Thesis metric: `thesis_metric.py`, `bge-m3` from the local Ollama, same-meeting
  Hungarian assignment. Validated before the screen on the thesis's own ES2015
  extraction (`es2015-explora-r3x`, 3 runs): pooled TP 63, 54, 42, 23, 10, 3, 0,
  0, 0 over tau 0.50-0.90, identical to the thesis's notebook 14; at tau 0.60,
  42 of 81 (recall 0.519, the thesis's 0.52) and F1 0.290.
- Wall-clock: probes 2189 s (36 min); screen 13073 s of model time (3.63 h),
  06:28 to 10:06; confirmatory 32204 s (8.95 h), 10:08 to 19:04.
- Raw runs are kept outside both repositories; nothing from the thesis data is
  committed here.

## Metrics (mean over 5 runs; min-max at tau 0.60)

R is thesis-metric recall, F1 thesis-metric F1. "Pooled TP" sums the 5 runs'
matches over 5 x the gold rows. Harness recall is the #1348 evidence-overlap rule.

### ES2015

| Model | R / F1 @0.50 | R @0.60 (min-max) | F1 @0.60 (min-max) | pooled TP @0.60 | R / F1 @0.70 | R / F1 @0.80 | harness recall (min-max) | harness recalled | failed meeting-runs | Decisions per meeting | s per run | language en / es / unknown |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `qwen3:8b` | 0.830 / 0.500 | 0.481 (0.444-0.556) | 0.290 (0.250-0.349) | 65 of 135 | 0.148 / 0.089 | 0.015 / 0.009 | 0.341 (0.222-0.407) | 46 of 135 | 0 of 20 | 15.70 (314 in 20 meeting-runs) | 167.5 | 314 / 0 / 0 of 314 |
| `qwen3:14b` | 0.926 / 0.658 | 0.548 (0.444-0.630) | 0.387 (0.324-0.421) | 74 of 135 | 0.059 / 0.042 | 0.022 / 0.016 | 0.393 (0.296-0.444) | 53 of 135 | 0 of 20 | 12.30 (246 in 20) | 215.9 | 246 / 0 / 0 of 246 |
| `qwen3.6:27b` | 0.756 / 0.651 | 0.563 (0.481-0.593) | 0.485 (0.413-0.542) | 76 of 135 | 0.222 / 0.191 | 0.022 / 0.019 | 0.452 (0.407-0.519) | 61 of 135 | 0 of 20 | 8.95 (179 in 20) | 580.8 | 179 / 0 / 0 of 179 |
| `qwen3.6:35b-a3b` | 0.919 / 0.650 | 0.652 (0.593-0.704) | 0.461 (0.413-0.554) | 88 of 135 | 0.252 / 0.177 | 0.015 / 0.011 | 0.541 (0.444-0.630) | 73 of 135 | 0 of 20 | 12.55 (251 in 20) | 89.6 | 251 / 0 / 0 of 251 |

### TS3009

| Model | R / F1 @0.50 | R @0.60 (min-max) | F1 @0.60 (min-max) | pooled TP @0.60 | R / F1 @0.70 | R / F1 @0.80 | harness recall (min-max) | harness recalled | failed meeting-runs | Decisions per meeting | s per run | language en / es / unknown |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `qwen3:8b` | 0.992 / 0.455 | 0.654 (0.577-0.692) | 0.300 (0.252-0.336) | 85 of 130 | 0.215 / 0.099 | 0.015 / 0.007 | 0.123 (0.077-0.154) | 16 of 130 | 0 of 20 | 21.90 (438 in 20) | 268.2 | 436 / 0 / 2 of 438 |
| `qwen3:14b` | 0.992 / 0.624 | 0.785 (0.692-0.846) | 0.493 (0.462-0.543) | 102 of 130 | 0.277 / 0.174 | 0.023 / 0.014 | 0.185 (0.154-0.231) | 24 of 130 | 0 of 20 | 14.20 (284 in 20) | 222.5 | 284 / 0 / 0 of 284 |
| `qwen3.6:27b` | 1.000 / 0.567 | 0.808 (0.692-0.885) | 0.457 (0.379-0.512) | 105 of 130 | 0.369 / 0.211 | 0.031 / 0.017 | 0.231 (0.192-0.308) | 30 of 130 | 0 of 20 | 16.55 (331 in 20) | 882.6 | 331 / 0 / 0 of 331 |
| `qwen3.6:35b-a3b` | 1.000 / 0.483 | 0.838 (0.769-0.885) | 0.405 (0.377-0.438) | 109 of 130 | 0.354 / 0.171 | 0.038 / 0.019 | 0.238 (0.192-0.346) | 31 of 130 | 0 of 20 | 20.40 (408 in 20) | 187.1 | 408 / 0 / 0 of 408 |

Full F1 curve (mean), tau 0.50 / 0.55 / 0.60 / 0.65 / 0.70 / 0.75 / 0.80 / 0.85 / 0.90:

| Model | ES2015 | TS3009 |
|---|---|---|
| `qwen3:8b` | 0.500 / 0.380 / 0.290 / 0.206 / 0.089 / 0.045 / 0.009 / 0.000 / 0.000 | 0.455 / 0.416 / 0.300 / 0.215 / 0.099 / 0.021 / 0.007 / 0.000 / 0.000 |
| `qwen3:14b` | 0.658 / 0.546 / 0.387 / 0.226 / 0.042 / 0.021 / 0.016 / 0.016 / 0.000 | 0.624 / 0.566 / 0.493 / 0.348 / 0.174 / 0.063 / 0.014 / 0.005 / 0.000 |
| `qwen3.6:27b` | 0.651 / 0.561 / 0.485 / 0.358 / 0.191 / 0.063 / 0.019 / 0.019 / 0.000 | 0.567 / 0.541 / 0.457 / 0.322 / 0.211 / 0.069 / 0.017 / 0.013 / 0.000 |
| `qwen3.6:35b-a3b` | 0.650 / 0.568 / 0.461 / 0.340 / 0.177 / 0.072 / 0.011 / 0.000 / 0.000 | 0.483 / 0.476 / 0.405 / 0.283 / 0.171 / 0.056 / 0.019 / 0.011 / 0.000 |

**The ordering changes across the curve**, as the protocol requires stating:
on TS3009 `qwen3:14b` has the highest F1 at 0.50 to 0.65 and `qwen3.6:27b` at
0.70 and 0.75; on ES2015 `qwen3:14b` is highest at 0.50, `qwen3.6:35b-a3b` at
0.55, and `qwen3.6:27b` from 0.60 to 0.70. `qwen3:8b` is lowest at every tau
from 0.50 to 0.65 on both series and at 0.70 on TS3009; on ES2015 `qwen3:14b`
falls below it at 0.70 (0.042 against 0.089).

## Selection rule

`dF1` = candidate mean F1 at 0.60 minus `qwen3:8b`'s; `S` = the larger of the
two models' per-run F1 min-max range at 0.60; condition 1 needs
`dF1 >= max(0.05, S)`.

| Candidate | Series | dF1 | need (S) | 1 margin | 2 recall >= `qwen3:8b` | 3 failures <= max(2, 0) | 4 volume <= 1.10 x `qwen3:8b` |
|---|---|---|---|---|---|---|---|
| `qwen3:14b` | ES2015 | 0.387 - 0.290 = +0.097 | 0.099 | **fail** (short by 0.002) | 0.548 >= 0.481 pass | 0 pass | 12.30 <= 17.27 pass |
| `qwen3:14b` | TS3009 | 0.493 - 0.300 = +0.192 | 0.084 | pass | 0.785 >= 0.654 pass | 0 pass | 14.20 <= 24.09 pass |
| `qwen3.6:27b` | ES2015 | 0.485 - 0.290 = +0.194 | 0.130 | pass | 0.563 >= 0.481 pass | 0 pass | 8.95 <= 17.27 pass |
| `qwen3.6:27b` | TS3009 | 0.457 - 0.300 = +0.157 | 0.133 | pass | 0.808 >= 0.654 pass | 0 pass | 16.55 <= 24.09 pass |
| `qwen3.6:35b-a3b` | ES2015 | 0.461 - 0.290 = +0.171 | 0.141 | pass | 0.652 >= 0.481 pass | 0 pass | 12.55 <= 17.27 pass |
| `qwen3.6:35b-a3b` | TS3009 | 0.405 - 0.300 = +0.105 | 0.084 | pass | 0.838 >= 0.654 pass | 0 pass | 20.40 <= 24.09 pass |

`qwen3.6:27b` (mean dF1 +0.176) and `qwen3.6:35b-a3b` (+0.138) both clear all
four conditions; `qwen3.6:27b` advances. `qwen3:14b` fails condition 1 on ES2015
(exactly: 0.38744 - 0.29039 = 0.09705 against S = 0.09884), although its
mean dF1 over the two series (+0.145) is above `qwen3.6:35b-a3b`'s. That is the
rule working as registered, not a statement that `qwen3:14b` is worse.

## Confirmatory run (n = 15)

Fresh runs, no screen run reused: `qwen3.6:27b` and `qwen3:8b`, 15 runs each on
both series, same frozen configuration, harness at `28ed7bed`. Both together
were forecast at 7.91 h (screen times: 15 x (580.8 + 882.6) s + 15 x (167.5 +
268.2) s = 21951 + 6536 s), inside the ~8 h budget, so the fresh comparator was
run as registered. Actual: 32204 s (8.95 h), 10:08 to 19:04; the overrun is
`qwen3:8b` on TS3009 (488.8 s per run against 268.2 s in the screen), where the
generation-cap runaway came back. 240 meeting-runs, all completed, exit 0.

#### ES2015

| Model | R / F1 @0.50 | R @0.60 (min-max) | F1 @0.60 (min-max) | pooled TP @0.60 | R / F1 @0.70 | R / F1 @0.80 | harness recall (min-max) | harness recalled | failed meeting-runs | Decisions per meeting | s per run | language en / es / unknown |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `qwen3:8b` | 0.822 / 0.502 | 0.474 (0.407-0.519) | 0.289 (0.255-0.322) | 192 of 405 | 0.136 / 0.083 | 0.010 / 0.006 | 0.341 (0.259-0.407) | 138 of 405 | 1 of 60 | 15.43 (926 in 60) | 165.7 | 921 / 0 / 5 of 926 |
| `qwen3.6:27b` | 0.800 / 0.654 | 0.575 (0.481-0.667) | 0.470 (0.394-0.554) | 233 of 405 | 0.225 / 0.183 | 0.035 / 0.028 | 0.457 (0.370-0.519) | 185 of 405 | 0 of 60 | 9.80 (588 in 60) | 597.6 | 588 / 0 / 0 of 588 |

#### TS3009

| Model | R / F1 @0.50 | R @0.60 (min-max) | F1 @0.60 (min-max) | pooled TP @0.60 | R / F1 @0.70 | R / F1 @0.80 | harness recall (min-max) | harness recalled | failed meeting-runs | Decisions per meeting | s per run | language en / es / unknown |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `qwen3:8b` | 0.987 / 0.462 | 0.646 (0.538-0.808) | 0.303 (0.248-0.400) | 252 of 390 | 0.226 / 0.106 | 0.010 / 0.005 | 0.146 (0.077-0.192) | 57 of 390 | 6 of 60 | 21.32 (1279 in 60) | 488.8 | 1278 / 0 / 1 of 1279 |
| `qwen3.6:27b` | 1.000 / 0.583 | 0.818 (0.692-0.923) | 0.474 (0.429-0.522) | 319 of 390 | 0.392 / 0.228 | 0.051 / 0.030 | 0.205 (0.115-0.346) | 80 of 390 | 4 of 60 | 15.90 (954 in 60) | 894.7 | 954 / 0 / 0 of 954 |

F1 curve (mean), tau 0.50 to 0.90:

| Model | ES2015 | TS3009 |
|---|---|---|
| `qwen3:8b` | 0.502 / 0.392 / 0.289 / 0.190 / 0.083 / 0.030 / 0.006 / 0.000 / 0.000 | 0.462 / 0.405 / 0.303 / 0.203 / 0.106 / 0.036 / 0.005 / 0.000 / 0.000 |
| `qwen3.6:27b` | 0.654 / 0.560 / 0.470 / 0.361 / 0.183 / 0.052 / 0.028 / 0.018 / 0.000 | 0.583 / 0.556 / 0.474 / 0.345 / 0.228 / 0.088 / 0.030 / 0.016 / 0.000 |

`qwen3.6:27b` is above `qwen3:8b` at every tau from 0.50 to 0.85 on both series
(both are 0.000 at 0.90); the ordering does not change across the curve.

| Series | dF1 | need (S) | 1 margin | 2 recall | 3 failures <= max(2, `qwen3:8b`) | 4 volume <= 1.10 x |
|---|---|---|---|---|---|---|
| ES2015 | 0.470 - 0.289 = +0.181 | max(0.160, 0.067) = 0.160 | pass | 0.575 >= 0.474 pass | 0 <= 2 pass | 9.80 <= 16.98 pass |
| TS3009 | 0.474 - 0.303 = +0.172 | max(0.093, 0.152) = 0.152 | pass | 0.818 >= 0.646 pass | 4 <= 6 pass | 15.90 <= 23.45 pass |

**Confirmed.** The n = 15 gains match the screen's (+0.194 and +0.157 at
n = 5). The margin also clears the 15-run min-max ranges, which are wider than
the 5-run ones.

Failures in the confirmatory run, by cause:

- `qwen3:8b`: TS3009a 6 of 15 runs, all `OllamaGenerationCapped` (the #1364
  runaway, back after 0 of 5 in the screen); ES2015c 1 of 15, unparseable reply.
- `qwen3.6:27b`: TS3009b 3 of 15 and TS3009c 1 of 15, all "unparseable reply"
  after the retry; no generation-cap failure. 4 of 60 is above the #1348 bar F
  (at most 3 of 60), which this registration does not apply but which a
  default decision would.


## Reading

- **Every candidate beats `qwen3:8b` on both metrics on both series.** Thesis
  F1 at 0.60 rises by 0.097 to 0.194; harness recall rises too (ES2015 0.341 to
  0.393-0.541, TS3009 0.123 to 0.185-0.238). The direction is consistent; the
  size at n = 5 was not established; the confirmatory run established it for
  `qwen3.6:27b` only.
- **The larger models produce less.** Decisions per meeting fall from 15.70
  (ES2015) and 21.90 (TS3009) for `qwen3:8b` to 8.95-12.55 and 14.20-20.40, so
  part of every F1 gain is precision. Condition 2 held for every candidate:
  recall at 0.60 rose as well, so none gained only by producing less.
- **No generation-cap failures in this screen**, for any model (0 of 20
  meeting-runs everywhere), `qwen3:8b` included. #1364 saw the cap on 11 of 15
  TS3009a runs with `qwen3:8b`, and the `qwen3:8b` timing probe hit it on its
  one TS3009a run; here 0 of 5 TS3009a runs did. At #1364's rate the chance of
  0 of 5 is (4/15)^5 = 0.0013, so something differs from #1364's runs (same
  harness code path and prompt; the cause was not investigated). The
  confirmatory run then saw it on 6 of 15 TS3009a runs: the runaway is not gone
  for `qwen3:8b`, and five runs were too few to see it.
- **Speed.** `qwen3.6:35b-a3b` is the fastest model measured (89.6 s and
  187.1 s per run against `qwen3:8b`'s 167.5 s and 268.2 s); `qwen3.6:27b` is
  3.5x and 3.3x slower than `qwen3:8b` (580.8 / 167.5, 882.6 / 268.2).

## Language, and how lenient tau 0.60 is (exploratory, not registered)

- **Every model wrote English.** Of 2451 produced subject-and-value texts, 2449
  read as English and 2 as undecided; none as Spanish. The gold
  `final_object: final_content` is Spanish.
- **The cross-lingual ceiling.** The English AMI sentence each gold decision
  was annotated from, embedded against its own Spanish `object: content`, has a
  median cosine of 0.689 on ES2015 (n = 27; 22 of 27 >= 0.60, 13 of 27 >= 0.70,
  3 of 27 >= 0.80) and 0.724 on TS3009 (n = 21 with a sentence; 20 of 21, 12 of 21,
  3 of 21). A perfect English paraphrase therefore rarely reaches 0.80: the
  collapse at tau >= 0.80 is the language gap, not the models.
- **A cross-team control.** Each model's ES2015 output scored against the
  TS3009 gold (meeting a to a, and so on; a different team designing the same
  AMI remote control) gets recall 0.315-0.354 at tau 0.60 (7 to 11 of 26 per
  run) and F1 0.185-0.297, and 0.023-0.115 recall at 0.70. At 0.60 the metric
  credits topical similarity a good share of the time: `qwen3.6:27b`'s control
  F1 (0.297) equals `qwen3:8b`'s real one (0.300). Differences between models
  at 0.60 are still meaningful (every real score is above its control), but
  absolute values at 0.60 overstate how many gold decisions were found, and
  0.70 separates real from control more sharply.
- **Recommendation only (no prompt was changed):** the extraction language is a
  lever for the thesis metric. Either asking the pass for Spanish subject and
  value on Spanish-gold evaluations, or scoring against an English rendering of
  the gold, should lift the reachable similarity above 0.80 and make the upper
  half of the curve informative. That is a separate registration; for OpenKOS
  itself the output language should keep following the source.

## Caveats

- n = 5 per model per series; one gold row is 0.037 (ES2015) or 0.038 (TS3009)
  of recall, and one extra match about 0.02 of F1 at `qwen3:8b`'s volume.
- Two development series, four meetings each, the same AMI design task.
- The thesis metric here uses only the same-meeting scope; the thesis's
  series-wide sensitivity was not computed.
- Embedding calls for interim scoring ran while `qwen3.6:27b` was generating;
  they add seconds to a few of its runs at most.

## Next steps (none taken here)

- Whether to change the decision pass's model is a maintainer decision this
  result informs but does not make. Against it: `qwen3.6:27b` costs 3.6x
  `qwen3:8b`'s time per run (597.6 / 165.7 on ES2015) and 1.8x on TS3009
  (894.7 / 488.8, where `qwen3:8b`'s runaways inflate its time), and its 4 of 60
  unparseable TS3009 meeting-runs would fail bar F.
- `qwen3.6:35b-a3b` also cleared every screen condition at about half
  `qwen3:8b`'s time; a confirmatory n = 15 of it (forecast 1.28 h) is the
  cheapest next measurement.
- The language lever above, and the 0.60 leniency, each need their own
  registration before they inform a threshold or a prompt.
