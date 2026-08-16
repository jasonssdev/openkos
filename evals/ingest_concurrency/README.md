# `ingest_concurrency` — costing #700's lever 4 before production pays for it

One question: **does issuing ingest's independent per-window extraction calls
concurrently buy wall clock, and does it cost quality?**

#700 measured ingest as inference-bound — 0.13s of client CPU against 47s of
wall clock — and ranked concurrency fourth of five levers, predicting a
sub-linear 1.3–1.6x because concurrent requests share one local GPU. #739 split
it out as the last lever still unmeasured, noting that #691's pinned 12288
context window had removed the memory blocker #700 recorded.

Levers 1 and 2 were measured and **rejected** (#728, #699); lever 3 was measured
and **not adopted** (#738). The standing rule applies here too: nothing is built
before its measurement exists.

Production is unchanged by this directory.

## The answer

**Not adopted — and, unlike levers 1–3, not refuted either. See `report.md`.**

The speedup is real but smaller than predicted (**1.26x** on the fan-out, which
is **91%** of an ingest, so **~20%** off a whole run), it **saturates at
concurrency 2** (3 and 4 are statistically indistinguishable from 2), and it
costs **no measurable quality** on either axis.

What blocks it is not evidence. It is that **a default Ollama serializes
concurrent requests**, so the entire gain is conditional on
`OLLAMA_NUM_PARALLEL` ≥ 2 — a setting on a separate, user-managed process that
openkos can document and perhaps detect, but never set. That is a decision
about accepting a dependency, not a measurement, and this directory cannot make
it.

## Finding 1 — a default Ollama serializes, so client threading buys nothing

Before measuring any speedup, the pre-probe asked whether the server this
project talks to serves concurrent requests in parallel at all.

Measured against the default server on this machine (Ollama 0.32.9, no
`OLLAMA_*` environment set), issuing N identical generations at once:

| concurrent requests | wall clock | per-call latencies | speedup |
| --- | --- | --- | --- |
| 2 | 10.2s | 5.1, 10.2 | **1.01x** |
| 3 | 15.2s | 5.1, 10.2, 15.2 | **1.01x** |
| 4 | 20.2s | 5.1, 10.2, 15.2, 20.2 | **1.01x** |

Those per-call numbers are the tell: each request finishes exactly one
solo-duration after the previous one. It is a perfect queue.

**Concurrency is therefore not a client-side lever.** Threading
`extract_concept_union`'s window loop against a stock Ollama would produce
identical wall clock and a more complicated codebase. Any speedup below
requires `OLLAMA_NUM_PARALLEL` raised on the server — a setting openkos does
not set, document, or ship, and cannot set on the user's behalf because the
server is a separate process the user runs.

Every arm in the main probe therefore records the server's
`OLLAMA_NUM_PARALLEL` as part of its identity (#738's rule). An arm that does
not name it cannot be told apart from one that was silently serialized.

## Finding 2 — the memory premise holds, but only because the speedup saturates

#739's premise: at an unpinned 32K context qwen3:8b occupied 10 GB, leaving no
room for a second slot on a 16 GB machine; #691 pinned the window to 12288 and
brought the same model to 7.2 GB, so ~11 GB of usable RAM now fits a second
slot.

Measured, that 7.2 GB is exactly right — and it is the **one-slot** number.
Ollama allocates a KV-cache slot per parallel request, so the footprint moves
with the knob finding 1 shows is mandatory: 7.2 / 9.1 / 11.1 / 12.9 GB at
`OLLAMA_NUM_PARALLEL` 1 / 2 / 3 / 4.

An earlier draft of this file concluded from the 12.9 GB figure that the
setting re-imposed the blocker #691 removed. **That was wrong, and it was wrong
because it generalized from the level nobody would use.** The speedup saturates
at 2 (see `report.md` §3), and two slots cost 9.1 GB — inside #739's own ~11 GB
budget. The memory objection dissolves precisely because more parallelism buys
nothing.

## How the arms are built, and why the probe stops where it does

`extract_concept_union` fans out one `_extract_once` call per window on the
chunked path (`concept.py:2925-2927`). Those calls are the only part of ingest
concurrency touches: the re-ask reads the merged result, the judge consumes it,
and both are single calls.

So the probe times exactly that loop — the real `_chunk_lines` windows fed to
the real `_extract_once` against a real model — and nothing else. It does not
reimplement the union pipeline, which would make every difference ambiguous
between the lever and the copy (the property `evals/title_first/` describes).

Concurrency 1 runs the same serial comprehension production runs, not a
one-worker pool, so the baseline is the shipped shape rather than a pool with
its overhead subtracted.

## Order is load-bearing, and it is why `map` is not `as_completed`

`_dedup_merged` keeps the **first** occurrence of a `(type, normalized title)`
key, and its docstring says the chunk order carries meaning — "earlier context
named the subject first".

Concurrent execution must therefore reassemble results in **window** order, not
completion order. The probe uses `ThreadPoolExecutor.map`, which preserves input
order. `as_completed` would silently change which duplicate wins and convert a
throughput experiment into a quality regression — and it would do so
invisibly, because the run would still produce a plausible object set.

`--self-test` asserts this property directly, with no model.

## The #694 band is NOT the pass mark here

The probe scores with the #694 oracle's fixture and its own `classify` /
`subject_for` — imported, not reimplemented, because that harness's precision
rule (judged positions as the denominator, first occurrence winning) is subtle
enough that a second implementation would quietly measure something else.

**But its recall band must not be read as a threshold for these numbers.** That
band scores the COMPLETE pipeline: union merge, re-ask, participant capture and
judge re-admission all recover subjects after the fan-out. This probe stops at
the fan-out plus `_dedup_merged`, so absolute recall sits far below the band on
every arm — the shipped serial one included, which a calibration run measured at
0.36 against the band's 0.80.

Reading that gap as a quality loss would manufacture a finding out of a
measurement boundary. #739 asks whether concurrency **changes** quality, and
that is answered arm-against-arm on identical inputs. The serial arm is the
control and the only baseline these columns may be read against.

## Running it

```
# a default server serializes; raise the setting on a scratch port so the
# user's own server is never touched
OLLAMA_HOST=127.0.0.1:11435 OLLAMA_NUM_PARALLEL=4 ollama serve &

python evals/ingest_concurrency/run_ingest_concurrency_probe.py \
    --runs 15 --host http://127.0.0.1:11435 --server-num-parallel 4
```

15 runs per arm, not 5. `evals/contradictions/` measured one arm against
**itself** at 0.44 and 0.19 on five runs each — wider than the gap it was
trying to report between two different models — and `evals/edge_typing/`
reversed its ranking between n=3 and n=15.

Arms are interleaved rather than blocked: running all of arm 1 and then all of
arm 2 confounds the arm with anything that drifts across a two-hour session —
thermal state, another process, a keep-alive expiring.
