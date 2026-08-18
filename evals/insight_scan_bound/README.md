# What does the unbounded near-duplicate scan cost per save? (#764)

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

| filed insights | scan | disk read | embed | payload | x baseline |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 0.000s | 0.000s | 0.000s | 0.1 KiB | -- |
| 1 | 0.132s | 0.000s | 0.132s | 0.1 KiB | 1.0x |
| 10 | 0.234s | 0.001s | 0.233s | 0.5 KiB | 1.8x |
| 25 | 0.403s | 0.001s | 0.402s | 1.2 KiB | 3.1x |
| 50 | 0.709s | 0.002s | 0.707s | 2.4 KiB | 5.4x |
| 100 | 1.277s | 0.004s | 1.273s | 4.8 KiB | 9.7x |
| 200 | 2.449s | 0.007s | 2.442s | 9.5 KiB | 18.6x |
| 400 | 4.774s | 0.014s | 4.759s | 18.8 KiB | 36.2x |
| 800 | 9.442s | 0.036s | 9.406s | 37.6 KiB | 71.6x |
| 1600 | 18.904s | 0.063s | 18.841s | 75.2 KiB | 143.3x |

**Linear, at ~11.8 ms per filed insight.** The first save into an empty bundle
pays 0.132s; the ten-thousandth would pay two minutes. There is no knee — the
cost never stops growing, which is exactly what #764 says.

**The disk half is not the cost.** `_filed_questions` reads and parses 1600
files in 0.063s while the embed call takes 18.841s: the read is 1/300th of the
scan. #764 asks the scan to "bound what it reads and embeds"; the measurement
says bounding the read alone would save nothing a user could perceive, and
bounding the batch is the entire fix. That is a correction to the issue's
framing, not to its conclusion.

**The payload is small in bytes and large in kind.** 75 KiB at 1600 insights is
nothing for a network, but every byte of it is a source question a user typed,
and on a remote `OLLAMA_HOST` all of it leaves the machine on every save.

## What shipped

`DUPLICATE_SCAN_LIMIT = 100`, chosen off the table above: it is where the
added wait stays near a second, and no bundle below it is truncated at all.
The scan now compares the 100 most recently filed insights and DISCLOSES that
it did — `? compared against the 100 most recently filed of 347 insights` —
because nothing here measured that a bounded scan finds what an unbounded one
would, so the bound is handed to the human rather than trusted.

This probe keeps measuring the UNBOUNDED curve after that landed: it passes an
explicit `limit`, and its self-test asserts the ladder still exceeds the
shipped cap. A probe that silently inherited the default would flat-line past
100 and quietly stop being evidence for the number it produced.

## Where the curve crosses a human threshold

| budget per save | first exceeded at |
| --- | ---: |
| 0.5s | 50 filed insights |
| 1s | 100 filed insights |
| 2s | 200 filed insights |
| 5s | 800 filed insights |

The scan runs at PREVIEW time — after the answer is on screen, while the human
waits at the confirmation gate — so this is dead time added to a write path
that cost nothing before #762.

## What this cannot say

**How many insights a real bundle accumulates.** There is no long-lived bundle
on disk to read that off, and it is a usage rate rather than a property of the
code: one insight per `query --save`, so the count is however many answers a
person chose to file. No harness can produce it. The cap therefore has to be
argued from where the cost turns uncomfortable, which is what the table above
is for.

**Whether a capped scan still finds the duplicates the full one would.** That
needs a population with many independent paraphrase relations; the stored
corpus has 13 unique questions and exactly two paraphrase pairs
(`evals/query_identity/` says the same about its own limits). Measuring recall
over two relations would produce a number with no power behind it, so this
probe does not produce one, and the cap must not be designed as though it had.

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
