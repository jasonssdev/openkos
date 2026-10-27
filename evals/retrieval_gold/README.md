# `retrieval_gold`: a retrieval gold set and a model-free harness

**Question.** How good is retrieval itself, apart from the chat model? Nothing in
`evals/` computed recall@k, MRR or nDCG against judged relevant documents; retrieval
was measured only through answers and citations (`query_citation`,
`query_sufficiency`, `query_attribution`), through paraphrase stability
(`retrieval_stability`) and through the display limit (`query_limit`). This harness
gives a retrieval technique a number that has no chat model in it
([#1609](https://github.com/jasonssdev/openkos/issues/1609)).

This directory holds the **harness and a DRAFT question set**. There are no relevance
judgments yet, so there is nothing to score and no baseline. The judgments come from
the blind sheet below.

## What it measures

The harness builds the bundle's derived stores through the production `reindex`, then
reads each channel through the same functions `answer()` calls. No production code
changed.

| Channel | Production seam | Notes |
| --- | --- | --- |
| `fts` | `answer._fts_search(index, answer._fts_query_terms(q), limit=10)` | Function words dropped (#648). |
| `dense` | `answer._dense_search(q, embedder, store, pool_limit=10)` | Embeds the full question. A degraded dense channel stops the run instead of scoring as zero. |
| `fused` | `fusion.select_top(fusion.fuse(fts, dense), k)` | The shipped order, computed once per `k` because the Source-share cap in `select_top` depends on `k`. |

`fts` and `dense` are read as one top-10 list and cut at `k`; they have no production
cut of their own. The deprecated and confidential filters `answer()` applies are applied
first. `--self-test` checks that the `fused` channel equals what `answer()` cites, at
every `k`.

Metrics, per channel, per question language, and per question kind:

- `recall@k` for k = 5, 8, 10. A document is relevant at grade 1 or 2.
- `mrr`, the reciprocal rank of the first relevant document in the top 10 (MRR@10).
- `ndcg@k` with graded relevance 0/1/2 and a linear gain.

`compare` runs a seeded paired bootstrap over questions for any two scored runs. The
exact definitions are in the module docstring of `gold_core.py`.

## The bundle and the questions

The bundle is committed material only, so a fresh clone rebuilds the same 35
documents: the 20 English notes of `evals/quiet_engine/corpus/sources/` (synthetic, and
stored as `Source` documents), the Spanish meetings, concepts and decisions of
`evals/query_grounding/grounding_corpus.py`, and the Spanish meeting
`examples/extraction-corpus/sources/medium-10-reunion-plataforma.md`. See
`gold_bundle.py` for why the English twin of the Spanish corpus is left out.

`questions.draft.json` has 36 questions: 22 English and 14 Spanish; single-hop,
multi-hop and enumeration. Each records its `language` and `kind`. It carries no
judgments, and the draft status means the questions may still change; a change
invalidates judgments written against the old set (the files carry a hash of it).

## The gold bundle: built by production `ingest`

The 35-document bundle above is **not to be judged**: a top 10 is 29% of it, half of it
is raw `Source` notes instead of compiled concepts, and each language is a different
domain ([#1609](https://github.com/jasonssdev/openkos/issues/1609)). The replacement is
compiled by the shipped `openkos ingest` and `openkos reindex` from the sources listed in
`bundle_sources.json`, with `build_bundle.py`. Nothing has been built yet; the build
needs the GPU, and the **questions and the full-pool judge sheet are rebuilt over the
built bundle afterwards** (the draft questions are written against the old bundle and
stay untouched until then).

### What is selected, and why

49 sources, every one pinned by sha256 in `bundle_sources.json`:

| Group | Sources | Language | Tier |
| --- | ---: | --- | --- |
| AMI meetings (`ami-meeting`) | 25 | en | core |
| Course notes (`course-notes`): `large-03`, `medium-08` | 2 | en | core |
| Course notes: `small-04-pre-build-skills` | 1 | es | core |
| Meeting minutes: `medium-10-reunion-plataforma` | 1 | es | core |
| Synthetic weather-station notes (`quiet_engine` corpus) | 20 | en | synthetic |

- **AMI** is the 12 development-series meetings (ES2015, IS1004, TS3009) plus the free
  pool of `evals/concept_model_screen/screen_data.py`, minus `TS3012a`. The loader runs
  every meeting through that module's fail-closed `require_allowed`, so a thesis
  evaluation or reserve meeting is refused when the list loads, before any file is
  opened. `TS3012a` is allowed by that list but refused here: production extraction hits
  the whole-source cap on it ([#1607](https://github.com/jasonssdev/openkos/issues/1607)).
- **Excluded on purpose:** `medium-09-sdk-skills-notes` (byte-identical to `medium-08`
  below its H1, so it would duplicate every derived object), the TS3005 transcripts under
  `evals/decision_extraction/` (a thesis evaluation series), and four sub-1 KB notes in
  `evals/desktop_app/` and `examples/good-life-demo/raw/`.
- **Target size: at least 200 compiled documents**, so that a top 10 is 5% of the bundle
  or less (it is 29% of the draft). Every `ingest` also writes one `Source` document, so
  the bundle keeps the realistic mix of `Source` and derived documents. Expected count,
  before `attach_at_ingest` merges any duplicate:

  | | Source docs | Derived docs | Total |
  | --- | ---: | ---: | ---: |
  | all 49 sources | 49 | about 250 | **about 300** (range 250 to 350) |
  | `--no-synthetic` (29 sources) | 29 | about 173 | **about 200** (range 170 to 230) |

  Derived counts: AMI 5.6 per meeting (the mean of the 17 meetings that have a stored
  run, range 2 to 12) times 25 is about 140; the four prose files are 33 in stored runs
  (10, 7, 7, 9); the 20 synthetic notes are an assumption of 3 to 5 each, because no
  stored run covers notes this small. Without the synthetic tier the bundle only just
  reaches 200, which is why it is listed as a product question below.
- **The synthetic tier is a trade.** It brings the bundle from about 200 to about 300
  documents and adds a third domain, but it is not realistic text (its manifest says it
  is synthetic). `--no-synthetic` drops it.

### The two languages do not share a domain

State this plainly before any question is written: **the allowed material cannot give
overlapping domains.** AMI is English and is about designing a remote control. The only
Spanish material that is allowed and realistic is two files (`medium-10`, a platform
review meeting, and `small-04`, a Spanish lesson on pre-built skills), about 21 KB
against about 900 KB of English. The Spanish documents are about 18 of about 300 (6%).

The one real overlap is `small-04` (es) with `large-03` (en): the same lesson, the
Spanish one condensed to about 45% (see `examples/extraction-corpus/README.md`). A
cross-language question can only be asked about that lesson. Everything else is a
language effect confounded with a domain effect, exactly as in the draft; a Spanish
question set over this bundle has about 18 documents to retrieve from.

### Building it

```bash
# No model: verify every selected file exists and matches its pinned sha256.
uv run python evals/retrieval_gold/build_bundle.py plan \
    --transcripts ~/Dev/Projects/openkos-eval-runs/concept-screen/inputs/transcripts \
    --prose-dir ~/Dev/Projects/openkos/examples/extraction-corpus/sources

# GPU-bound: init a fresh workspace, `ingest --auto` each source, `reindex`, write the manifest.
uv run python evals/retrieval_gold/build_bundle.py build \
    --workspace ~/openkos-eval-runs/retrieval-gold/bundle-v1 \
    --transcripts ~/Dev/Projects/openkos-eval-runs/concept-screen/inputs/transcripts \
    --prose-dir ~/Dev/Projects/openkos/examples/extraction-corpus/sources
# --limit N ingests at most N pending sources (a pilot); --no-synthetic drops the tier.
```

`--transcripts` is the `concept-screen/inputs/transcripts` layout (AMI is not committed).
Most of `--prose-dir` is gitignored third-party notes, so it points at a checkout that
has them.

- **Outside the repository.** `build` refuses a workspace inside this repository or
  inside any git work tree, because `ingest` auto-commits.
- **Shipped defaults.** `init` takes the packaged defaults (`qwen3:8b`, `bge-m3`), and
  the transcript decision pass stays off (its packaged default). The manifest records
  `openkos.yaml` verbatim so a later change of default cannot pass for this build.
- **Resumable.** `gold-build/state.json` (beside `raw/` and `bundle/`, never inside the
  bundle) records each source. A `done` source is skipped; a failed one is retried. A
  source left `started` means a run died inside `ingest`, and re-ingesting accumulates
  objects rather than replacing them, so the build stops and names it instead of
  guessing. A source whose bytes changed since it was ingested also stops the build.
- **Manifest.** `gold-build/manifest.json`: each source with its sha256, status and
  seconds; the openkos commit and version; the chat and embedding model tags with the
  digests the local Ollama reports; the config; document counts by folder; whether the
  build is `complete`. It is written after a failure too, with the honest statuses.

### GPU forecast

About **2.4 hours** of wall-clock for all 49 sources (range 2.2 to 2.9 hours), about
**2.1 hours** with `--no-synthetic`, with the shipped `qwen3:8b` at default config.
Ingest is inference-bound (`docs/cli.md`, "How long ingest actually takes"), so the unit
is model calls. Measured inputs come from stored `concept-screen`, `window-screen`,
`runtime-screen` and `judge-study` records (`qwen3:8b`, uncached calls only):

| Piece | Basis | Seconds |
| --- | --- | ---: |
| 23 chunked AMI meetings, 218 windows | 31.4 s per window, the whole union run's wall divided by its windows (n=40 uncached runs, p90 37.5 s) | 6,850 |
| 2 unchunked AMI meetings (`IS1004a`, `IS1005a`) | 2 extractions (25.8 s) + judge (9.5 s) + participant capture (19.6 s) | 160 |
| 3 prose files below the chunk threshold | 2 extractions + judge, about 61 s each | 180 |
| `medium-10` (meeting-shaped, 4 windows) | 4 windows at 31.4 s | 125 |
| 20 synthetic notes | about 61 s each: an upper bound, since the 25.8 s mean is from 13 to 17 KB documents | 1,220 |
| Embeddings in `reindex` | about 330 calls at an assumed 0.1 to 1.0 s | 150 (33 to 330) |
| Model load and ingest bookkeeping | assumed | 60 |
| **Total** | | **about 8,750 (2.4 h)** |

Assumptions, all of them:

1. The AMI records were run with the meeting id as the title; `ingest` on the file may
   decide meeting-shape from the title and content differently. Window counts come from
   the production `_chunk_lines` on the stored bodies (224 windows over 25 meetings), but
   whether `IS1004a` and `IS1005a` fan out in `ingest` follows `fans_out` on the file as
   ingested, not on the bare id.
2. 8 of the 25 meetings have no stored run; they take the mean. The stored per-window
   wall already contains the participant-capture, judge and re-ask calls.
3. The records were measured with nothing else on the GPU. A shared server, or a thermal
   state different from those runs, moves every row.
4. `transcript_decisions` is off (packaged default). Turning it on adds one call per
   window.
5. No stored measurement exists for a `bge-m3` embed of a 12,000-character chunk; the
   0.108 s in `evals/insight_scan_bound/` is a short question. The row is small either
   way.
6. The cap-failure rate (4 capped windows and 2 timeouts in 738 stored calls, about
   260 s each) is inside the per-window mean. `TS3012a`, which fails reliably, is
   excluded.
7. `ingest` runs no judge-model stage (`gemma4:26b-a4b` serves only `adjudicate`,
   `contradictions` and the Ask sufficiency check), so there is no model swap.

## What it cannot show

- **Pool bias.** Judging every document for every question is not feasible, so a
  judge grades only the *pool*: the union of the top 10 of each channel. A document
  that was never in a pool is unjudged and **counts as not relevant**. The consequence
  is one-sided. A technique that retrieves a relevant document no pooled channel
  retrieved is scored as if it had retrieved noise, so new techniques are penalized
  against the channels that built the pool. `score` prints `judged@10`, the share of
  each channel's top 10 that has a judgment. Read a number only next to it, and
  re-pool (a new sheet, judged on the new candidates only) when a candidate's
  `judged@10` is low.
- **The `fts-only` pool.** Until embeddings can run, the pool is the lexical top 10
  alone. Judgments written from it say nothing about documents only the dense channel
  finds, so they favour `fts` over `dense` and `fused`. The sheet header and the
  judgments file both name the pool mode; do not report a dense or fused number
  against an `fts-only` pool as a baseline.
- **A 35-document bundle.** A top 10 is 29% of the bundle, so recall@10 is easy and
  ranks mostly by who is first. The set discriminates order, not reach, until a larger
  bundle replaces it.
- **Two languages, one domain per language.** English questions run over a hardware
  project and Spanish ones over meetings; cross-language retrieval is not measured.
- **No answer quality, no abstention.** Every question is answerable from the bundle.
- **Not a pre-registration.** The baseline and every candidate technique need their own
  pre-registration (arms, metric, bar, `n`) before any run. This directory has none.

## Commands

Run everything from the repository root. Paths for sheets, keys, runs and judgments must
be **outside** the repository; the harness refuses a path inside it.

```bash
# No model contacted. Also run by `uv run python evals/run_self_tests.py`.
uv run python evals/retrieval_gold/run_retrieval_gold.py --self-test

# Blind judgment sheet from the lexical pool (no model, works today).
uv run python evals/retrieval_gold/run_retrieval_gold.py judge-sheet \
    --out ~/openkos-eval-runs/retrieval-gold/sheet-fts.md
# The key (opaque candidate code -> document) is written next to it as
# `sheet-fts.md.key.json`. It holds no retrieval path; keep it away from the
# adjudicator anyway.

# Retrieve every question (needs Ollama serving bge-m3; zero chat calls).
uv run python evals/retrieval_gold/run_retrieval_gold.py run \
    --out ~/openkos-eval-runs/retrieval-gold/run-baseline.json
# Lexical only, no model: add --fts-only.

# Full pool: top 10 of fts, dense and fused, from a run with embeddings.
uv run python evals/retrieval_gold/run_retrieval_gold.py judge-sheet --pool full \
    --run ~/openkos-eval-runs/retrieval-gold/run-baseline.json \
    --out ~/openkos-eval-runs/retrieval-gold/sheet-full.md

# A second adjudicator's sheet on a seeded sample of the questions.
uv run python evals/retrieval_gold/run_retrieval_gold.py judge-sheet --pool full \
    --run ~/openkos-eval-runs/retrieval-gold/run-baseline.json --sample 12 \
    --seed 4242 --out ~/openkos-eval-runs/retrieval-gold/sheet-second.md

# Filled sheet -> judgments; with --second, Cohen's kappa (plain and weighted).
uv run python evals/retrieval_gold/run_retrieval_gold.py adjudicate \
    --sheet ~/openkos-eval-runs/retrieval-gold/sheet-full.md \
    --second ~/openkos-eval-runs/retrieval-gold/sheet-second.md \
    --out ~/openkos-eval-runs/retrieval-gold/judgments.json

# Score a run, then compare two scored runs (seeded paired bootstrap).
uv run python evals/retrieval_gold/run_retrieval_gold.py score \
    --run ~/openkos-eval-runs/retrieval-gold/run-baseline.json \
    --judgments ~/openkos-eval-runs/retrieval-gold/judgments.json \
    --out ~/openkos-eval-runs/retrieval-gold/scores-baseline.json
uv run python evals/retrieval_gold/run_retrieval_gold.py compare \
    --base ~/openkos-eval-runs/retrieval-gold/scores-baseline.json \
    --cand ~/openkos-eval-runs/retrieval-gold/scores-candidate.json \
    --channel fused --metric ndcg@10
```

`adjudicate` rejects a sheet with an unlabelled, unknown or out-of-range entry and
writes nothing. `score` rejects a judgments file with an unknown document id, a missing
question, a label outside {0, 1, 2}, a question with no relevant document, or a stale
question set, and scores nothing. Disagreements between two adjudicators are listed to
be resolved by hand; the command never merges them.

## Files

| File | Role |
| --- | --- |
| `run_retrieval_gold.py` | Retrieval through the production seams, the commands, `--self-test`. |
| `gold_core.py` | Metrics, bootstrap, kappa, the question and judgment contracts, the sheet. Pure. |
| `gold_bundle.py` | The 35-document draft bundle, built from committed material. Not to be judged. |
| `build_bundle.py` | Builds the gold bundle with production `ingest`; `plan`, `build`, `--self-test`. |
| `bundle_sources.json` | The selected sources with their sha256, language, kind and tier, and what was excluded and why. |
| `questions.draft.json` | The draft question set; no judgments. |
