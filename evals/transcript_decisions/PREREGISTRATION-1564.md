# Pre-registration: does the re-ask after a generation cap recover? (#1564)

**Status: REGISTERED 2026-10-20, before any model call of this measurement.**
The rules below are fixed. A change after this point is a new registration, not
an edit to this one; any deviation is disclosed in `RESULTS-1564.md`. The harness
(`run_cap_reask.py`, and `--retry-after-cap` in `run_transcript_decisions.py`) is
committed before this file and was exercised only by its model-free `--self-test`.

## Question

When the decision pass's FIRST ask of a window hits the generation cap
(`BackendGenerationCapped`), how often does the production re-ask return a
non-capped, parseable reply, and what does that re-ask cost in wall time?

The answer decides whether production should keep or skip the re-ask after a
cap (`run_decision_pass(retry_after_cap=...)`, default `True` today). **This
branch changes no production default.** It records the verdict and what the
production change would be.

## Why

The decision pass re-asks any window whose call raised or returned unparseable
text. After a cap the re-ask repeats a ~250 s runaway when it caps again. The
stored runs cannot answer this (#1564's last comment): the per-attempt `call_log`
(#1565) is absent from every stored file, and the harness hardcoded
`retry_after_cap=False`. What they suggest, inferred from wall time only: 7 of 7
windows that capped twice, and about 8 of 15 re-asked windows that likely
recovered. That ~53% is an inference about mostly one window of one meeting, not
a measurement, and it is a prior here, not a result.

## Population and design

Model: `qwen3:8b` (the production default), digest recorded in the stamp.
Frozen configuration: decision-pass prompt hash recorded in the stamp, generation
ceiling 8192, context window 12288, `chat_timeout` 600 s, the production
`run_decision_pass` with `retry_after_cap=True`. Only the harness differs from
production: it asks ONE window per call of the pass, which is the same prompt and
the same retry logic (windows are asked independently; nothing carries over).

Meetings: the AMI development meetings the analysis names, TS3009a, ES2015d,
TS3009b, TS3009d (6, 9, 11 and 10 windows = 36 windows). Held-out series are not
touched; no gold is read, since nothing here is scored for recall.

1. **Sweep.** Every window is asked once, with the production re-ask. A window
   whose first ask capped is *cap-prone*. If a sweep finds none, one more sweep is
   run (at most two); if still none, the result is "no cap observed" and the
   question is unanswerable at this population.
2. **Replay.** The cap-prone windows are re-asked round-robin (each a fresh first
   ask, with its re-ask when it caps) until the target is reached.

A *trial* is one first ask and, when production would re-ask, its second ask.
Cap events from both phases are pooled for the verdict and also reported by phase
and by window. Replaying only cap-prone windows is selection on the first-ask
outcome, which is what the question conditions on (a cap occurred); the cost is
that the verdict is a rate **for the windows that cap**, and the cap-prone set may
be a few windows of one or two meetings. RESULTS states the per-window breakdown.

**n target: 15 capped first asks** (pooled). **Stop** at the first of: 15 capped
first asks; 60 replay asks; 9000 s (2.5 h) of model time. Stopping on the count
of capped first asks does not depend on whether the re-ask recovered, so it does
not bias the recovery rate.

## Definitions

- A call is `capped` when it raised `BackendGenerationCapped`, `ok` when it
  returned a parseable reply (an empty `[]` is a valid answer and counts as ok),
  `unparseable` otherwise.
- **Recovered**: first ask `capped` AND second ask `ok`.
- **Items**: verbatim-verified Decision items the window contributes after the
  re-ask (`len(result.results)` for the one-window pass).
- **Re-ask seconds per recovered item** = mean wall time of all re-ask calls
  following a cap, divided by (recovery rate x mean items per recovered re-ask).

## Decision rule (fixed)

Let n be the capped first asks, k the recovered, L and U the 95% Wilson interval
of k/n.

- **KEEP the re-ask** (no production change) if `L >= 0.30` AND the re-ask
  seconds per recovered item `<= 250`.
- **SKIP the re-ask after a cap** (production would pass `retry_after_cap=False`
  from the concept wiring; that is a separate change, not made here) if
  `U < 0.30`, OR `k = 0`, OR the re-ask seconds per recovered item `> 250`.
- Otherwise **INCONCLUSIVE**: the default stays, and RESULTS says how many more
  capped first asks the same rate would need.

Justification of the bars, chosen before any data:

- **0.30 on the lower bound.** A re-ask that recovers fewer than about three caps
  in ten is not worth a second chance at a reply that has just run away; and 0.30
  is the lower bound of 8 of 15, so a replication of the stored-data inference at
  the target n just clears it, while a true rate near 0.2 or below is rejected.
  Disclosed bias: the bar was set knowing that inference, so it favours KEEP if
  the inference is right. The upper-bound SKIP clause is the symmetric guard.
- **250 s per recovered item.** 250 s is one runaway's cost (the cap at 8192
  tokens). A re-ask that costs more than one extra runaway for each item it wins
  back is a worse trade than not asking; below it, the time buys items at a price
  the pass already pays for a normal window.
- The time condition is a point estimate, not an interval: with n = 15 its
  interval would be too wide to separate anything, so RESULTS reports it beside
  the recovery interval and says if a verdict hangs on it.

## Forecast (Ollama wall time)

Inputs from stored `qwen3:8b` runs: a normal window ~8-10 s; a capped call ~250 s
(8192 tokens); capped-first windows were 15 of the stored re-asked records; the
inferred cap rate of a cap-prone window ~40% per ask; inferred recovery ~50%.

| Stage | Calls | Forecast |
|---|---|---|
| Sweep, 36 windows (+ ~2 caps) | 36 + re-asks | ~10 min base + ~2 x 250 s + re-asks = ~0.4 h |
| Replay to 15 capped first asks (~28 more asks at a 40% cap rate) | 28 first + ~13 re-asks | 13 x 250 + 15 x 10 + 13 x (0.5 x 250 + 0.5 x 25) = ~1.6 h |
| **Total** | | **~2.0 h**, ceiling 2.5 h by the budget stop |

If the observed cap rate in the sweep is below ~20%, 15 events would exceed the
ceiling; the budget stop then ends the run short and RESULTS reports the achieved
n of 15 with the interval it supports, rather than extending the budget.

## Honest limits stated in advance

- One model, one machine, one population of 36 windows; the replayed windows are
  a handful that happen to cap. The verdict is for them, not for caps in general.
- A re-ask right after a runaway on the same server may benefit from a warm prompt
  cache (faster prefill, same generation); not controlled.
- Ollama serializes; nothing else uses it during the run.
- No recall is scored. Recovered items are counted, not judged for quality.
