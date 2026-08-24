# generation_thinking — where a runaway actually goes (#830)

**Verdict: #830's proposed detector does NOT clear #830's own bar in either
arm, and its premise is wrong about this failure. The runaway is not a repetition loop in
the reply — it is unbounded deliberation in the `thinking` channel, where the
reply is EMPTY. Nothing is shipped and no production file is touched.**

[#830](https://github.com/jasonssdev/openkos/issues/830) proposes moving
`chat()` to streaming and aborting on an n-gram repetition loop, on a stated
premise:

> That is the shape of a degenerate generation — the model entering a
> repetition loop on a particular input — not of a reply that is legitimately
> long. **Length is a proxy for it; repetition is the thing itself.**

and sets its own kill criterion:

> A candidate detector ships only if it cuts those runaways materially
> shorter while cutting **zero** of the 60+ legitimate replies the same
> harness has recorded — and the false-cut count is the number that kills it,
> not the time saved.

This directory measures both before anything is built.

```
uv run python -u evals/generation_thinking/run_generation_thinking_probe.py --self-test
uv run python -u evals/generation_thinking/run_generation_thinking_probe.py --runs 10
uv run python -u evals/generation_thinking/run_generation_thinking_probe.py \
    --rescore evals/generation_thinking/results/runs-20260823T232849Z-qwen3-8b.json
```

`--self-test` and `--rescore` make no model calls. Both fixtures are the
committed public ones `evals/generation_runaway/` uses, so nothing private is
involved and every number below regenerates from the stored sweep for free.

## 1. The tokens do not go into the reply

A cut-off run at the shipped `think` default carries a reply of **zero
characters** and thousands of characters of `thinking`. One such reply,
tail of its reasoning:

> *"But the user's instructions say to prefer fewer, richer objects. Maybe
> the Project is the main object... Alternatively, the source is primarily
> about the Project... But the user's example mentions that a meeting
> transcript should include both..."*

That is not a repetition loop. It is the model deliberating past its budget
and never emitting an answer — and a detector reading the streamed reply has
nothing to read, because there is no reply.

## 2. `think` is what turns a cut-off into a TOTAL LOSS

40 runs, `qwen3:8b`, `num_predict` 2048, `num_ctx` 12288, arms **interleaved
run by run** so nothing that drifts over the sweep is confounded with the
arm.

| arm | fixture | n | cut off | **TOTAL LOSS** | median s | median thinking chars |
| --- | --- | --- | --- | --- | --- | --- |
| `no-think` | `helios-overview` | 10 | 0 | 0 | 8.1 | 0 |
| `no-think` | `kickoff` | 10 | 2 | **0** | 18.4 | 0 |
| `think` | `helios-overview` | 10 | 0 | 0 | 29.6 | 4 307 |
| `think` | `kickoff` | 10 | 2 | **2** | 24.3 | 3 278 |

TOTAL LOSS is a cut-off carrying no content — the paid call that produced
nothing, which is the 222-second failure #828 measured. **Both of them are
`think` runs, and both `no-think` cut-offs returned usable content.** The
cut-off RATE is the same in both arms here (2 of 10); what changes is
whether a cut-off costs you the whole call.

Latency separates on `helios-overview` (29.6s against 8.1s) and much less on
`kickoff` (24.3s against 18.4s), so "thinking is slower" is supported and
"thinking roughly doubles latency" is not.

Two caveats on that sentence, both about this stored sweep:

- **The interleaving covered one axis, not both.** Arms alternated call by
  call, but `fixture` was the outer loop, so the twenty kickoff rows ran as
  one solid time block before the twenty helios-overview rows — the same
  exposure the arm axis paid to remove, one loop up. The two per-fixture
  deltas that sentence compares were therefore measured in disjoint time
  windows, and a cross-FIXTURE conclusion inherits whatever drifted between
  them. The probe now interleaves fixtures run by run as well; this stored
  sweep predates that fix and its cross-fixture comparison should be read
  with the same caution the ordering section below applies to rates.
- **One `helios-overview` `no-think` row is a order-of-magnitude outlier.**
  Run 10 returned 64 tokens / 304 chars in 1.7s where the other nine rows
  cluster at 313–334 tokens / 1327–1622 chars / 7.7–8.2s, all `stop`. It is
  a legitimate reply by every rule this probe applies, and it enters the
  8.1s median (which, being a median, barely moves) — but that cell is nine
  comparable calls plus one degenerate one, and a reader of the table could
  not have known.

### The ordering mattered, and that is a finding about the method

An earlier sweep of these same cells ran every `think` call before every
`no-think` call. It reported `think`/`kickoff` at **7 of 10 cut off and a
median of 50.5s**, against 2 of 10 and 24.3s here. That artifact was
discarded when the ordering was fixed, so those numbers are recorded as an
observation rather than as a committed result — but they are why the probe
now interleaves, and why the `n` caveat at the bottom of this file is not
boilerplate. **Ten runs per cell is not enough to state a rate.**

## 3. The detector OVERLAPS in both scorable cells

`detector.repetition_share` is the n-gram duplicate share #830 names,
computed over the reply content, and scored per arm AND per fixture —
pooling either one lets the lowest cut-off and the highest legitimate reply
come from populations with nothing to do with each other.

`helios-overview` produced no cut-offs in either arm, so it has no verdict.
The two cells that can be scored:

| arm / fixture | class | n | rep min | rep max | replies with no content |
| --- | --- | --- | --- | --- | --- |
| `no-think` / `kickoff` | cut off | 2 | **0.321** | 0.728 | 0 |
| `no-think` / `kickoff` | legitimate | 8 | 0.043 | **0.364** | 0 |
| `think` / `kickoff` | cut off | 2 | **0.000** | 0.000 | 2 |
| `think` / `kickoff` | legitimate | 8 | 0.000 | **0.125** | 0 |

**Both OVERLAP, and the bar is zero false cuts.**

- Under `think` — the configuration openkos ships — every cut-off scores
  **0.000**, because both have no content to score at all, while a
  legitimate reply reaches 0.125. The detector has no signal whatsoever on
  the failing case it exists for.
- Under `no-think` it looks promising until the right comparison is made.
  The *lowest*-scoring cut-off is **0.321** and a legitimate reply reaches
  **0.364**. A threshold catching both cut-offs also cuts that good reply.
  Comparing maxima (0.728 against 0.364) reads as separation and is the
  wrong comparison for a zero-false-cut bar.

The probe's first draft made both mistakes it now refuses: it pooled the
arms and compared maxima, and printed `SEPARATES` over a cell where the
detector is blind. The self-test pins a 0.294-under-0.300 pair as `OVERLAPS`
so the comparison cannot drift back.

## What this does NOT establish

- **That `think=false` should ship.** Its extraction-QUALITY cost is
  unmeasured here. This probe counts cut-offs, losses and seconds; it says
  nothing about whether the objects extracted without deliberation are as
  good — and 4 307 characters of deliberation per call are presumably
  buying something. That A/B is the gate, and this repo has a standing rule
  against adopting an inference-level change on latency alone.
- **That no detector can work.** It establishes that *this* detector, on
  *this* signal, does not clear the bar on 40 runs of one model. A detector
  reading the `thinking` channel is a different proposal and is not measured
  here.
- **That streaming is unnecessary.** Streaming would still stop paying for
  tokens after an abort. What it no longer has is a validated abort
  condition.

## n

40 runs, two public fixtures, one model, one ceiling — and **two cut-offs
per scorable cell**, which is a thin basis for the 0.321 and 0.000 figures.
They are reported as two rather than as a rate.

The re-sweep above is the reason to take that seriously: the same cell moved
from 7 of 10 cut off to 2 of 10 between two sweeps of ten runs. Every count
in this file should be read as "what these ten runs did", and any conclusion
that needs a RATE needs more of them.

What the small n does not weaken is the shape: every TOTAL LOSS in both
sweeps was a `think` run, and every one of them carried zero content — which
is the observation the detector's blindness follows from.

## The A/B this file named as the gate — run (2026-08-24), and `think=false` does NOT ship

`run_think_quality_ab.py` measures the extraction-quality cost this file
said was unmeasured, at the scale this file said ten runs was not: **15
interleaved runs per fixture per arm through the FULL shipped pipeline**
(`extract_concept_union`, judge and participant capture included) at the
shipped `num_predict` 8192 / `num_ctx` 12288, on the same two public
fixtures. The `no-think` arm injects `"think": false` at the transport seam
(`OllamaClient` takes `urlopen` as a constructor argument), so production
code is untouched and the `think` arm's request stays byte-identical to
what every shipped call sends.

The pre-registered bar: `think=false` ships only if no fixture's median
anchor recall drops AND pooled failed runs do not increase. It fails the
second clause:

| arm | fixture | n | failed | med anchors | med objects | med s |
| --- | --- | --- | --- | --- | --- | --- |
| `think` | `helios-overview` | 15 | 0 | 4 of 4 | 7 | 18.3 |
| `think` | `kickoff` | 15 | **1** | 4 of 5 | 9 | 45.0 |
| `no-think` | `helios-overview` | 15 | 0 | 4 of 4 | 7 | 16.9 |
| `no-think` | `kickoff` | 15 | **2** | 4 of 5 | 11 | 40.7 |

**VERDICT: KEEP `think`** — equal median recall everywhere, and MORE
failed runs (2 against 1, every one an `OllamaGenerationCapped` on
`kickoff` at 218–239 s).

Three observations that close the question rather than merely losing it:

- **The runaway relocates; it does not die.** Under `think=false` the
  deliberation this directory measured going into `thinking` goes into
  CONTENT instead, and the ceiling cuts it exactly as before. The runaway
  follows the SOURCE (`kickoff`, on both arms), which is what #830's own
  measurement said, now reconfirmed under the opposite `think` setting.
- **The sibling probe's "usable content" does not survive the pipeline.**
  A single-call cut-off under `no-think` carried usable text; in the
  shipped pipeline `OllamaGenerationCapped` raises before that content is
  used, so a capped run costs the whole call in BOTH arms and the
  total-loss asymmetry this file measured stops mattering where it counts.
- **`no-think` over-produces.** Median 12 completed-run objects against 9
  on `kickoff` (19 twice, 20 once against anchors that need 5) — the
  deliberation is buying selectivity, which is presumably why equal anchor
  recall costs more objects without it.

With this, both of #830's candidate remedies are measured and dead: the
repetition detector (this file) and the `think` lever (this section). What
stands is what already shipped — #848 names whichever bound actually bound
— and #830's own asymmetry argument for leaving the ceiling at 8192.
