# Pre-registration: does the dense channel favour long documents? (#1648)

**Status: DRAFT. Registered when this file merges, before any measured run.** A
change after the merge is a new registration, not an edit. Part of #1652. Nothing
in this file has been run: no model was contacted, and the bundle and question set
it is measured on (the v1 set of #1609) do not exist yet. Items marked
**Owner confirms** carry a recommended default and are the only open choices.

## The question

`VectorStore.query` (`src/openkos/state/vectorstore.py`) over-fetches
`k * max(chunk_count)` rows and keeps each document's MINIMUM chunk distance. A
long document is embedded as several chunks (`state/reindex.py`,
`_EMBED_CHUNK_TARGET = 12_000` characters), so it gets several chances to land a
close chunk; a Decision has one vector. #1648 asks whether that favours long
documents enough to cost retrieval.

This is a hypothesis about a cause, not a finding. Two things make it less
obvious than it reads:

- The chunk-count range is narrow. From the pinned source sizes
  (`bundle_sources.json`, `ceil(bytes / (12,000 - 150))`, an estimate of the
  chunker, not a measurement) 24 of the 61 sources are one chunk, 37 are two to
  five, and a derived document is expected to be one (the v1 count comes from
  `chunk-counts`). "Long" here means two to five chunks.
- A `Source` is long and also broad, so it is easy to confuse a LENGTH effect
  with a TYPE effect. The diagnostic below is built to tell them apart.
- At fusion, `select_top` already caps Sources at `max(1, k // 2)` slots (#1220),
  so a dense bias can be partly absorbed downstream.

## Data

Development series only, from the repository: the v1 gold bundle built by the
production `ingest` (`build_bundle.py`, #1609), its v1 questions and adjudicated
judgments (frozen, identified by `questions_sha256`), and the passages file of
#1646 for delivered evidence. No thesis evaluation series, no held-out or reserve
series, nothing from `evals/decision_extraction/` TS3005, and never the 14
downstream questions of #1652 (they motivate the problem; they are not a test
set). The issue's "without regressing the held-out part" is **out of scope here**:
held-out confirmation, if ever wanted, is a separate registration the owner opens.

Prerequisites, each checked before the first read of a number: the v1 judgments are
adjudicated and the question set hash recorded; the passages file loads
(`delivered` refuses an unverifiable span); the baseline `run` was made at a
recorded commit with the shipped defaults (`bge-m3`, no flag changed). The
baseline run uses the embedder only, zero chat calls.

**Floor on `n`.** The diagnostic is confirmatory only with at least **40
questions** and at least **20 long documents** (two or more chunks) in the bundle.
Below either it is descriptive and cannot start Step 2.

## Step 1: the diagnostic (model-free, defined now)

Computed by `prereg_tools.py chunk-counts` then `length-diagnostic` from the stored
baseline run, the judgments and the chunk counts of the built bundle (the
production chunker, `reindex._chunk_embed_texts`). No model call. Every statistic is
taken over slots held by NON-RELEVANT documents (grade 0 or unjudged), which is how
relevance is controlled: a long document that ranks well because it is relevant
cannot move them. Questions are the resampling unit (10,000 resamples, seed 1609,
95% percentile interval).

| | Definition |
| --- | --- |
| D1 | Spearman rho between a non-relevant document's chunk count and its rank in the list, pooled over questions. Negative = more chunks, earlier. Reported overall and within `Source` only. |
| D2 | Enrichment: long (two or more chunks) non-relevant slots observed, over the number expected if long documents were drawn at their base rate among that question's non-relevant documents. 1.00 = no effect. |
| D3 | Displacement gain: mean recall@5 of the dense top-10 list, and again after every non-relevant long document is deleted from it. The difference is an upper bound on what any length remedy can buy inside the pool (the deletion backfills perfectly). |
| D4 | Share of slots by document type, beside the share of gold documents by type. |
| D5 | Relevant share of slots, by chunk bucket (1, 2, 3 or more). |

Labels read: `dense@5`, `dense@10`, `fused@5`, `fused@8`. The decision label is
**`dense@5`**. D3 is computed for the dense channel only (the fused list is cut per
`k` by the Source cap, so a deletion has nothing honest to backfill from).

**Rule to proceed to Step 2.** All three on `dense@5`:

1. D2: the lower 95% bound of the enrichment ratio is above **1.25**.
2. D3: the displacement gain is at least **0.03** (recall@5).
3. D1 within `Source` only: the upper 95% bound of rho is below **0**.

If (1) and (2) pass and (3) fails, the effect follows the document TYPE, not its
length: record "type effect", run no Step 2 arm (a chunk-count remedy would not
touch it), and any Source-prior idea needs its own registration. If the rule fails,
record the numbers and close #1648 as **not supported on this fixture**: no
treatment is run, no bar is retuned.

The harness has to be able to come back empty: `--self-test` includes a run where
the long documents rank last (enrichment 0.75, gain 0.00), and the boundary of each bar
(strict or inclusive) is pinned by a test.

## Step 2: treatments (only if Step 1 says proceed)

Arms are scored by the existing `score` and `compare` commands over the same
questions and judgments as the baseline.

| Arm | Change | Re-embed |
| --- | --- | --- |
| A0 baseline | The shipped dense score: minimum chunk distance per document | none |
| **T1** (primary) | Mean of the two smallest chunk distances (`agg_mean_top_m`, m = 2); a one-chunk document keeps its own distance | none |
| T2 | Minimum distance plus `lambda * log2(chunk_count)`, with `lambda` = 0.10 times the median over questions of the baseline top-10 distance spread (`penalty_lambda`), printed and recorded before any metric is read | none |
| T3 | Chunk target 4,000 characters (extraction's window) with the shipped minimum | full |

Why T1 is primary: it has no free parameter, so there is nothing to tune on the
development set. T2 has `lambda`; T3 changes the embedding and so needs the model
tag gate (`EMBED_COMPOSITION_TAG` would have to change) to rebuild users' stores.
**T2 and T3 are exploratory: they are reported, and cannot be adopted on their own
result.** A promising exploratory arm gets its own registration. Scoring chunks all
the way through fusion (the issue's third remedy) needs a change to `fusion.py` and
is not run here.

**T3 is two-sided.** If the length hypothesis is true, more chunks per Source under a
minimum should make the bias worse. A T3 that improves recall while D2 rises is
evidence the bias is not harmful, and is reported as such.

**Implementation, not built yet.** T1 and T2 are scored from exact L2 distances
(the metric `vec0` uses) between the question and EVERY chunk row of the built
`vectors.db`, brute force (a few hundred rows), through the production
`answer._dense_search` replaced by a harness-local function. `rescore`,
`agg_mean_top_m`, `agg_penalty` and `penalty_lambda` exist and are tested; the
`run --chunk-distances` wiring that reads the rows is built, under TDD with the stub
embedder, before the first Step 2 run. `src/` does not change for the experiment.
Ties break by concept id, as in production.

## Metrics, margins and the decision rule

Primary (all dev questions, `fused`, paired bootstrap through `compare`, seed 1609,
10,000 resamples): **nDCG@5**, T1 minus A0.

An arm is **adopted for implementation** only if all of these hold:

| # | Condition |
| --- | --- |
| P | nDCG@5 gain of at least **+0.02**, lower 95% bound above 0 |
| G1 | `delivered` at `fused@8` (#1646) does not fall: delta of at least 0 questions. `delivered_any` and the loss classes are reported beside it |
| G2 | recall@8, for `enumeration` questions, falls by no more than 0.03; the same for each language |
| G3 | Pool bias: the `judged@10` of the candidate's fused top 10 is at least 0.90, else re-pool (judge only the new candidates) before reading any number |

Also reported, not gated: recall@5, recall@8, MRR, nDCG@8, the `dense` channel alone,
the type share of the context (D4), and the query-time cost (none for T1 and T2).

Why +0.02: D3 is the most a remedy can buy; the margin is two thirds of its bar, so
a remedy that captures most of the ceiling clears it and one that captures a sliver
does not.

**One shot.** T1 either clears P and G1 to G3 or it does not. No second bar, no
retry under a tuned `lambda`, no extra arm after seeing a result. A positive
estimate with a lower bound at or below 0 is "inconclusive", which is not adoption.

Adoption means a production PR with its own tests and ADR, not a merge of this
experiment. Production wiring (a change to `VectorStore.query`) is one function; it
must keep `VectorStore` a Protocol and the fused order for a bundle with no multi-chunk
document byte-identical.

## Cost forecast: the embedding re-index

T1 and T2 re-embed nothing: they re-read the stored chunk rows. The cost is T3.

| | Chunk rows from 61 sources | Derived documents (one row each) | Embeds per fresh build | At 0.1 to 1.0 s |
| --- | ---: | ---: | ---: | ---: |
| 12,000 (shipped) | about 152 | about 317 | about 470 | 47 to 470 s |
| 4,000 (T3) | about 384 | about 317 | about 700 | 70 to 700 s |

Inputs and what is not known: the chunk rows are the `bundle_sources.json` byte sizes
over the window less a 150-character header, not the production chunker's output; the
317 derived documents are `README.md`'s forecast (range 330 to 430 in all); 0.1 to
1.0 s per embed is that README's assumption, and there is no measurement for a
12,000- or 4,000-character `bge-m3` call (the stored 0.108 s is a short question). The
harness embeds a whole fresh bundle on each `run`, so T3 is **about 12 minutes of GPU in
the worst case**, once. Before any GPU time, replace the estimates with the model-free
count: `prereg_tools.py chunk-counts --bundle <bundle-v1>/bundle` and again with
`--chunk-target 4000`.

If T3 were ever adopted, a user's workspace re-embeds every Source through the model
tag gate: roughly `(rows at 4,000 - rows at 12,000) + unchanged` embeds, about 2.5x the
Source rows, a one-time cost proportional to Source bytes (about 250 embeds per MB at 4,000
characters), at the same unmeasured per-call time.

## Threats, said now

- **Pool bias.** The judgments were pooled from the baseline channels, so a candidate that
  retrieves a relevant document no baseline channel did is scored as noise. G3 is the
  guard; it is not a cure.
- **Type is confounded with length.** Every long document is a `Source` today, which is why
  rule (3) exists and why a "type effect" is a named outcome.
- **EN/ES twins.** Twelve meetings exist in both languages, so a long English Source and its
  Spanish twin can both be in a pool. The language split is reported.
- **n.** The v1 question count decides what +0.02 means. The interval is the decision, not
  the point estimate, and Step 1 states its floor.

## Harness status

| Piece | State |
| --- | --- |
| `chunk-counts`, `length-diagnostic` | built, `--self-test` (this PR) |
| `rescore`, `agg_mean_top_m`, `agg_penalty`, `penalty_lambda` | built, `--self-test` (this PR) |
| `run --chunk-distances` (exact per-chunk distances) | **not built**; built before Step 2 |
| `run` with `_EMBED_CHUNK_TARGET = 4000` (T3) | **not built**; a harness-side override, not a `src/` change |

```bash
# no model: chunk counts of the built bundle (and the T3 forecast)
uv run python evals/retrieval_gold/prereg_tools.py chunk-counts \
    --bundle ~/openkos-eval-runs/retrieval-gold/bundle-v1/bundle \
    --out ~/openkos-eval-runs/retrieval-gold/chunk-counts.json
uv run python evals/retrieval_gold/prereg_tools.py chunk-counts \
    --bundle ~/openkos-eval-runs/retrieval-gold/bundle-v1/bundle --chunk-target 4000
# no model: Step 1, from the stored baseline run
uv run python evals/retrieval_gold/prereg_tools.py length-diagnostic \
    --run ~/openkos-eval-runs/retrieval-gold/run-baseline.json \
    --judgments ~/openkos-eval-runs/retrieval-gold/judgments.json \
    --chunk-counts ~/openkos-eval-runs/retrieval-gold/chunk-counts.json \
    --out ~/openkos-eval-runs/retrieval-gold/length-diagnostic.json
```

## Owner confirms (recommended default first)

1. **Primary arm: T1.** Default T1 (no free parameter). Alternative: T2 as co-primary, at the
   price of a multiplicity correction and a `lambda` chosen on development data.
2. **`delivered` gate: non-inferior (delta of at least 0).** Default non-inferior, because
   delivered evidence is bounded by trimming (#1647), which this change does not touch.
   Alternative: require an improvement, as the issue's "done when" reads; at the v1 `n`, one
   question is noise.
3. **Margin: +0.02 nDCG@5.** Default as derived above; raise it if the v1 question count is
   large enough that +0.02 is a small effect.
