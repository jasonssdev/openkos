# Type coverage over AMI `TS3005a` / `TS3005b` — 2026-08-06

`qwen3:8b`, 3 runs per source, real `extraction.concept.extract_concept` seam.
Reproduce with the two commands in [README.md](README.md).

Filed as [#454](https://github.com/jasonssdev/openkos/issues/454).

> **This supersedes a first run whose transcripts were silently truncated.**
> The segment→word range resolver indexed only lexical elements, so any range
> whose endpoint landed on a `<disfmarker>` or `<vocalsound>` was dropped —
> about a fifth of every transcript. `TS3005a` went from 229 to 288 turns and
> `TS3005b` from 524 to 694 once fixed. The numbers below are the complete
> inputs; the earlier ones described a corpus that was never measured on
> purpose.

## Result

| Source | Size | Objects per run | Types emitted (3 runs) |
|---|---:|---|---|
| `TS3005a.summary` | 1.1 KB | 4, 4, 4 | `Concept` 3 · `Decision` 3 · `Event` 3 · `Project` 3 |
| `TS3005a.transcript` | 16.4 KB | **1, 1, 5** | `Concept` 2 · `Decision` 1 · `Event` 3 · `Project` 1 |
| `TS3005b.summary` | 2.5 KB | 1, 1, 1 | `Project` 3 |
| `TS3005b.transcript` | 40.8 KB | 1, 1, 1 | `Event` 3 |

**The cap is never involved.** `_MAX_OBJECTS_PER_SOURCE` is 6 and every run
reports `n proposed, n retained`. Nothing was truncated away.

## Two findings, and the second is the sharper one

**1. `TS3005b` collapses to one object in both variants.** Its 40.8 KB
transcript yields a single `Event` in all three runs, and its 2.5 KB summary a
single `Project`. Neither is a cap effect.

**2. `TS3005a.transcript` is unstable: 1, 1, 5.** Same 16.4 KB input, same
model, same prompt — one run produced five objects reaching `Decision` and
`Project`, two produced one. That variance is not noise around a mean; it is
the difference between a usable bundle and a useless one, from identical
inputs.

The first finding alone would suggest a size ceiling. The second says the
pipeline can do better on the same input and often does not, which is a
different problem with a different fix.

## What works

`Decision` is reachable, from the transcript as well as the summary. Reading
this report as "the classifier cannot leave `Concept`" would overstate it.

## Absences the corpus does not explain

| Type | Annotated mentions | Objects emitted |
|---|---:|---:|
| `Person` | 17 (`TS3005a`), 3 (`TS3005b`) | **0** |
| `Organization` | 4 (`TS3005a`), 1 (`TS3005b`) | **0** |
| `Place` | 3 (`TS3005a`) | **0** |

Zero across four sources and twelve runs, against annotations written by AMI's
team before this project existed. The "corpus contained none" explanation is
ruled out by evidence that cannot have been tuned toward this result.

Open question that could narrow this: whether `Person`/`Organization`/`Place`
are meant to be emitted for a *participant in* a meeting at all, or only for a
subject the document is *about*. If the latter, these absences may be correct
behaviour and only the collapse and the variance are defects.

## The size hypothesis, weakened

Size still correlates loosely — the 40.8 KB source never exceeds one object —
but `TS3005a.transcript` at 16.4 KB produced five in one run and one in two
others, so size cannot be the whole mechanism. #379's corpus also contains
13–17 KB documents that produced 5–10 objects each.

A single `Event` for a 40.8 KB meeting suggests the model is *summarising* the
source rather than *enumerating* its subjects — a prompt-level failure rather
than a cap or parsing one. Untested.

## What this says about #379

That gate measured multiplicity and closed it: 8 sources → 13–19 derived
objects, median 3.5 per source. The measurement was correct for what it
sampled — one kind of corpus, edited technical prose about software — and the
conclusion was then carried as if it were about extraction in general.

On meeting material the same pipeline reaches 1:1 routinely and unpredictably.
"Multiplicity holds" needed a second corpus shape before it was a claim about
the extractor rather than about that corpus.
