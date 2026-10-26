# Pre-registration: are the objects the selector judge cuts redundant? (#1579, step 1b)

**Status: REGISTERED 2026-10-21, before any redundancy number was computed.**
The only look at the data before this commit was a coverage probe that counted
whether the ceiling-stage texts exist in the embedding cache (0 of 564 distinct
texts missing); it read no vector and computed no similarity. A change after
the commit that adds this file is a new registration, not an edit to this one.

## Question

`CUT_PRECISION_RESULTS.md` (step 1) found the cut objects about as
source-supported as the kept ones: inconclusive by the registered rule, and G1
measures support only. Does the cut buy something else: removing
**near-duplicates** of objects that are kept? If cut objects are markedly more
redundant against the kept set than kept objects are against each other, the
cut deduplicates. If not, it removes distinct content.

Measurement only: no production code and no default changes. Any loosening of
the selection needs its own ADR.

## No model calls

- Inputs: the #1571 replay records (`judge-study/records/`) and the screen's
  `bge-m3` embedding cache (`concept-screen/cache/embed.sqlite`), both read
  only. Embeddings are digest-checked (`drop_ablation.EMBED_DIGEST`) and come
  from the cache alone: the embedding function raises on a miss.
- A cache miss means **not measurable**: the harness reports the count of
  missing texts for that record and does not embed. No Ollama, no judge, no
  extractor call. Every command runs with `OLLAMA_HOST` pointing at a closed
  port.

## Population

- **Primary:** round 1, run 1, pool DA (5 meetings), all five extractors:
  25 meeting-runs. Seed index 0.
- **Secondary (reported, not decisive):** run 2 for `qwen3:8b` and
  `qwen3.6:27b` (10 more meeting-runs); seed indices 1 and 2 (point estimates
  only).
- **Arms:** `self` and `fixed` (`gemma4:26b-a4b`), as in step 1. For the
  extractor `gemma4:26b-a4b`, whose two arms are one sample, both arms read the
  `fixed` records.
- **Objects:** the `ceiling` stage (the judge's input), partitioned into KEPT
  (in the arm's `judge` stage) and CUT by the step-1 `partition` (type, title,
  description, window; multiset). Unlike step 1, objects with **no window are
  included**: redundancy needs no source excerpt. Their count is reported.

## Measure

- **Text:** `title: description` (`unit_text`), the cached vector, cosine of
  unit vectors.
- **Redundancy of an object** `R(x)` = the maximum cosine from `x` to any KEPT
  object of the **same meeting-run** other than `x` itself (by ceiling index).
  - CUT objects: all kept objects of the meeting-run.
  - KEPT objects (the baseline): the other kept objects of the meeting-run.
  - An object with an empty reference set (no other kept object) is excluded
    and counted.
- **Near-duplicate:** `R(x) >= 0.85`. Fixed here, before any similarity was
  seen; there is no prior in the repository for this embedding on this text
  shape. Sensitivity at 0.80 and 0.90 is reported, not decided on.
- **Statistic:** `D = share_neardup(CUT) - share_neardup(KEPT)`, pooled over
  objects. Beside it, not decided on: the mean `R` of each group, and the
  share of CUT objects with `R < 0.85` (cut objects that remove distinct
  content, as `n of TOTAL`).
- **Interval:** the two-stage cluster bootstrap the screen uses (meetings with
  replacement, then items within a drawn meeting; 10,000 resamples; seed 42;
  percentile 95%). An item is one (extractor, window) group of objects
  (windowless objects form one group per extractor and meeting-run), carrying
  (cut near-dups, cut objects, kept near-dups, kept objects).
- **Sensitivity (reported, not decided on):** the reference set restricted to
  the **same window**; objects with no window have no same-window reference and
  are excluded there.

## Decision rule

Per judge arm, primary population pooled over the five extractors, seed 0.
Margin 0.10 (ten points of near-duplicate share).

0. **Insufficient** if fewer than 30 CUT objects have a reference set.
1. **The cut buys deduplication** if the lower bound of `D` is above +0.10.
2. **The cut removes distinct content** if the upper bound of `D` is below
   +0.10 **and** the share of CUT objects that are near-duplicates is below
   0.50 (most cut objects are not near-duplicates of anything kept).
3. **Inconclusive** otherwise.

Overall: **dedup** if both arms say rule 1; **distinct content** if both say
rule 2; otherwise **mixed or inconclusive**, with the pattern described.
Per-extractor figures and the secondary population carry their intervals and
are not decisive.

Consequences, stated before the data:

- Dedup: the cut has a measured job; #1579 step 2 keeps the selection and
  considers a cheaper deterministic dedup in its place.
- Distinct content: together with step 1, the cut costs lift without buying
  support or deduplication; step 2 may propose loosening (needs an ADR).
- Mixed or inconclusive: recorded as such. No default changes.

**Limits stated in advance.** Near-duplicate is an embedding proxy on one
model and one threshold. Redundancy against the kept set excludes redundancy
among cut objects. "Vague" objects are not measured. Distinct is not the same
as valuable: gold loss is not estimated here (the L10 stage-loss table of
#1559 is not in this checkout, so the optional gold-loss split is out of
scope). If more than 10% of primary records are not measurable (cache miss),
the run is reported as degraded and not decided.
