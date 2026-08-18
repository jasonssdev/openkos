# What does the near-duplicate scan cost per save? (#764)

Embedding calls only — zero chat calls. Every question it embeds was already
generated and stored by `evals/query_title/`.

```
uv run python -u evals/insight_scan_bound/run_insight_scan_bound_probe.py --self-test
uv run python -u evals/insight_scan_bound/run_insight_scan_bound_probe.py
uv run python -u evals/insight_scan_bound/run_insight_scan_bound_probe.py --rescore <points.json>
```

## The question

#762's near-duplicate disclosure shipped as an unbounded scan: every
`query --save` reads every `*.md` under `bundle/insights/` and embeds the whole
set in one batched call. #764 asks for a bound and says it must be DISCLOSED
rather than silent, but names no number, and nothing in this repository had
measured the cost the bound is supposed to bound.

A cap chosen without a curve is a cap invented. This measures the curve.

## Result, 2026-08-18

`bge-m3` on a local Ollama, median of 3 runs per point, synthetic bundle whose
questions cycle the 170 real stored filings.

| filed insights | cold scan | WARM scan | disk read | payload | cold/warm |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.119s | **0.108s** | 0.000s | 0.1 KiB | 1x |
| 10 | 0.222s | **0.107s** | 0.001s | 0.5 KiB | 2x |
| 50 | 0.690s | **0.113s** | 0.002s | 2.4 KiB | 6x |
| 100 | 1.283s | **0.119s** | 0.004s | 4.8 KiB | 11x |
| 200 | 2.452s | **0.129s** | 0.008s | 9.5 KiB | 19x |
| 400 | 4.800s | **0.152s** | 0.015s | 18.8 KiB | 32x |
| 800 | 9.479s | **0.200s** | 0.031s | 37.6 KiB | 47x |
| 1600 | 18.820s | **0.285s** | 0.074s | 75.2 KiB | 66x |

**COLD** re-embeds every filed question, which is what shipped before the
cache: linear at ~11.8 ms per insight, no knee. **WARM** is what ships now --
every filed question already embedded, so the save pays one embed for the new
question plus disk and comparison.

| budget per save | cold first exceeds | warm first exceeds |
| --- | ---: | ---: |
| 0.5s | 50 filed insights | never, at any measured size |
| 1s | 100 | never |
| 2s | 200 | never |
| 5s | 800 | never |

**The warm curve never crosses half a second**, including at 1,600 filed
insights where the cold one takes nearly nineteen. At that size warm is 0.285s
and decomposes exactly as predicted: 0.108s for the one embed, 0.074s of disk,
0.086s of comparison.

**That is what retired the bound.** #764's cap compared only the 100 most
recently filed, which bought the cost with a recall loss NOTHING COULD MEASURE
-- whether a duplicate survives truncation depends on where it sits in filing
order, a usage rate no fixture produces. With the cache the whole bundle is
compared for less than the cap used to cost, so the unmeasurable question does
not need an answer; it stops existing.

**The disk half is now the second-biggest term, and still small.** At 1,600
insights the read is 0.074s against 0.086s of comparison. Both are dwarfed by
the single embed. Bounding either would save nothing a person could perceive.

## What shipped, and what replaced it

**First**, `DUPLICATE_SCAN_LIMIT = 100` — compare the 100 most recently filed,
and disclose the truncation. That bought the cost with a loss NOTHING HERE
COULD MEASURE.

**Then the bound was retired**, because the cost had a better fix. A stored
question does not change and neither does its embedding, so caching them
replaces the linear EMBED term with the linear COSINE term — and this probe's
own numbers put those ~220x apart — 11.8 ms to embed one filed question
against 0.053 ms to compare one. The warm column above is the result: every
filed insight compared, for less than the cap used to cost.

The cold column is therefore the measurement of a design that no longer ships.
It is kept because it is the evidence that retired itself: the same curve that
justified the bound is the one that showed the bound was the wrong trade.

This probe keeps measuring BOTH arms. Its self-test asserts the cold arm still
sends the whole bundle and the warm arm sends exactly one text — without that
second check, a cache that silently stopped being read would report the cold
path in the warm column and nothing would notice.

## What this cannot say

**How many insights a real bundle accumulates.** There is no long-lived bundle
on disk to read that off, and it is a usage rate rather than a property of the
code. It no longer decides anything: the warm curve is flat enough that the
answer does not change the design.

**Whether the scan finds the duplicates it should.** That is the SIGNAL's
recall, not the scan's, and `evals/query_identity/` measures it: 11 of 35
paraphrase pairs at the shipped threshold. Removing the bound removed a
SECOND, compounding loss on top of it -- the one that depended on filing
order -- but it did not improve the signal.

**Whether the slope holds elsewhere.** One embedding model, one local Ollama,
one machine. A default Ollama serializes embeddings, so the per-text cost is
the deployment's, not openkos's — a parallel backend would change the slope.
It would not change the linearity, which is what makes the scan unbounded.

## Why a synthetic bundle is the honest instrument

Cost is a function of insight count and source-question length. The lengths are
real (drawn from the stored filings), the count is constructed. Questions repeat
once the ladder passes the stored population, which inflates how many duplicates
are found and does not touch what is measured — the cost of comparing, not the
outcome of a comparison. The `candidates` column is recorded to prove the scan
actually ran, never as a quality signal.
