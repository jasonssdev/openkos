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

61 sources, pinned by sha256 in `bundle_sources.json`; the 12 Spanish translations
are pinned only after they are produced and reviewed (see "The Spanish translations"):

| Group | Sources | Language | Tier |
| --- | ---: | --- | --- |
| AMI meetings (`ami-meeting`) | 25 | en | core |
| Spanish machine translations of the 12 development meetings (`ami-translation`) | 12 | es | core, pending |
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
  | all 61 sources | 61 | about 317 | **about 380** (range 330 to 430) |
  | `--no-synthetic` (41 sources) | 41 | about 240 | **about 280** (range 250 to 310) |

  Derived counts: AMI 5.6 per meeting (the mean of the 17 meetings that have a stored
  run, range 2 to 12) times 25 is about 140; the four prose files are 33 in stored runs
  (10, 7, 7, 9); the 20 synthetic notes are an assumption of 3 to 5 each, because no
  stored run covers notes this small. Without the synthetic tier the bundle only just
  reaches 200, which is why it is listed as a product question below. With the
  translations the core tier alone is about 280 documents. Derived counts for the
  translations reuse the English rate (5.6 per meeting, 67 in all); a Spanish
  transcript may extract differently.
- **The synthetic tier is a trade.** It brings the bundle from about 200 to about 300
  documents and adds a third domain, but it is not realistic text (its manifest says it
  is synthetic). `--no-synthetic` drops it.

### Language and domain

Without the translations the allowed material cannot give overlapping domains: AMI is
English and about designing a remote control, and the only Spanish material that is
allowed and realistic is two files (`medium-10`, a platform review meeting, and
`small-04`, a Spanish lesson on pre-built skills), about 21 KB against about 900 KB of
English. A language effect would be confounded with a domain effect, exactly as in the
draft.

The 12 translations remove that: every development meeting exists in both languages,
so a question can be asked of the same content in English and in Spanish, and a
cross-language question (asked in one language, answered by a document in the other)
has a twin for each of the 12 meetings. The Spanish side is then about 100 of about
380 documents (26%), instead of 18. The other 13 AMI meetings and the synthetic notes
stay English only, and `small-04` stays the one human-written overlap.

What it does not give: the Spanish is machine-translated, so its register is the
translator's, not a Spanish speaker's. A Spanish-side effect can partly be a
translation-quality effect; the owner's sample review (below) bounds that, it does not
remove it.

### The Spanish translations

`translate_meetings.py` translates the 12 MCD-AFG thesis **development** meetings
(ES2015, IS1004 and TS3009, a to d; `config/corpus.toml`, `[oe1].development_series`,
ADR 0005) with a local model through the production `OllamaClient`. Evaluation, held-out
and reserve series are refused, and so is every meeting outside those 12, including the
free-pool meetings and TS3012a (`build_bundle.require_translatable`, which layers the
12-meeting list over `screen_data.require_allowed`; the check runs before any file is
opened or directory created).

- **Windows.** The body is cut by production `concept._chunk_lines` (about 4,000
  characters, never inside a line, and a speaker turn is one line). Each window goes
  out as its `LABEL: text` lines and must return the same number of lines with the same
  labels in the same order. The blank-line layout of the English file is reused, so the
  Spanish file has the same shape.
- **Retry, never drop.** A reply that is capped, times out, has the wrong structure, or
  is still English is retried once with a different seed (temperature 0.2, so the seed
  matters), then recorded as failed with both attempts' reasons. A meeting with a failed
  window is not written at all. A server that stops answering stops the run (exit 3) and
  records no failures; the same command resumes. `--retry-failed` re-translates only the
  failed windows.
- **Structural check on every output** (windows, then the whole meeting): same number of
  turns, same speaker sequence, no empty turn (a turn must be `LABEL: text` with text),
  and no English left untranslated beyond the threshold below. The meeting's turn count
  is also checked against the turn index of the source.
- **Untranslated-English threshold.** A turn is *English left* when the repo's ES/EN
  function-word vote (`concept` `_ES_FUNCTION_WORDS`, `_EN_FUNCTION_WORDS`, its tokenizer
  and its 2x margin, the voter behind `_dominant_language`) gives at least **2 English
  votes** and more than twice the Spanish votes, or when it is the English source back
  again (4 or more words). Only turns of **4 or more words** are counted; shorter turns
  (`Okay.`) carry too few function words to vote on. A window with more than **10%** of
  its countable turns left in English is retried; a meeting with more than **2%** is not
  written. Measured with `--self-test` on committed text: 0 of 89 genuine Spanish lines
  (`medium-10-reunion-plataforma.md`) are flagged, and 401 of 507 English lines
  (`evals/quiet_engine/corpus/sources`) are (79%; at 3 votes recall drops to 66%). The
  detector misses English with fewer than 2 function words, which is why the owner
  sample exists. The 2% and 10% figures are choices, not measurements; the manifest
  records the real per-meeting share. **They stay as they are for the IS1004a pilot and
  are reviewed with its data (the recorded per-window and per-meeting shares, and which
  turns were flagged) before the full run.**
- **Resumable and pinned.** One record per window under `windows/<meeting>/`; the
  manifest holds each source sha256, the model tag and digest the local Ollama reports,
  the prompt sha256, the options, per-window attempts with seconds and `eval_count`, and
  every failure. A run directory refuses to resume under a different model, prompt or
  options, and the English transcripts must match the sha256 pinned in
  `bundle_sources.json`.
- **Attribution.** AMI is CC BY 4.0, and a translation is an adaptation. Each file's
  frontmatter carries the source, the licence and its links, the AMI citation
  (Carletta, J. et al., 2006, *The AMI Meeting Corpus: A Pre-announcement*, MLMI 2005,
  LNCS 3869, 28-39, DOI 10.1007/11677482_3), `translation_of`,
  `machine_translated: true`, the model tag, and a plain statement that it is a machine
  translation, not proofread in full, that every utterance was translated and that
  speaker labels and turn order are unchanged.
- **Distinct titles.** The files are `<meeting>-es.md`, so `ingest` titles them from the
  file name (the transcripts have no heading) and the Spanish and English twins never
  share a title or a slug.

### Which model, and why

**Chosen: `gemma4:26b-a4b`** (digest `001e5dafc3c7`, installed), confirmed by the owner.
No screen was run for this; the choice rests on stored evidence. The Spanish is neutral
(the prompt's default), also the owner's decision.

For:

- Spanish output language held in the one screen that measured it on a Spanish source:
  0 of 16 wrong-language titles for `gemma4:26b-a4b`, against 2 of 13 for
  `qwen3.6:35b-a3b` (dropped on that veto) and 1 of 29 for `qwen3:14b`
  (`evals/concept_model_screen/RESULTS.md`, round 1, V3).
- Fast enough for long output: Ollama decode 32.2 tokens per
  second on `gemma4:26b-a4b` (a mixture-of-experts model with about 4B active
  parameters) against 31.2 for `qwen3:8b`, with prefill 614 against 299 tokens per
  second (`evals/runtime_screen/RESULTS.md`). Dense `qwen3.6:27b` took 47 s per
  extraction window against 15 s (`RESULTS.md`, round 1) and `qwen3:14b` 28 s.
- It is already the Ask sufficiency model chosen in #1560 and is resident-sized for this
  machine (stage-0 memory 17.8 GiB).

Against, and what covers it:

- In #1560 round 1 it copied an English example sentence verbatim into 21 of 30 Spanish
  answers (#1583). A translation prompt that leaves English in place would fail the same
  way. The prompt quotes no sentence to copy and states the target language; the
  untranslated-English check and the owner sample exist for exactly this.
- No repo measurement of its Spanish translation quality exists. The evidence above is
  about Spanish output language, not fidelity. That gap is why the sample review
  precedes the pin.
- The not-chosen models: `qwen3.6:35b-a3b` (fast, 8.5 s per window, but dropped for
  wrong-language titles), `qwen3:14b` and `qwen3.6:27b` (slower, no better on language),
  `qwen3:8b` (the production ingest model: translating and then extracting with the same
  model would also couple the two steps). `mistral-small3.2:24b` and `gemma2:27b` are
  installed and plausible, but nothing in the repo measured them on Spanish, so
  proposing either would be a guess.
- `ollama` serves one large model at a time; the run swaps `gemma4:26b-a4b` out for
  `qwen3:8b` before ingest (a load of about 10 s, `runtime_screen`).

### Order of operations

All commands from the repository root. Outputs go outside it. Nothing below runs a model
except `run` and `build`.

```bash
T=~/Dev/Projects/openkos-eval-runs/concept-screen/inputs/transcripts   # AMI transcripts
R=~/openkos-eval-runs/retrieval-gold/translations

# 0. No model. Everything the script checks about itself.
uv run python evals/retrieval_gold/translate_meetings.py --self-test

# 1a. Translation pilot (GPU): the smallest meeting, IS1004a. Then STOP: redo the
#     translation forecast from its recorded seconds and eval_count, and review the
#     10% / 2% English-leak limits against its per-window and per-meeting shares.
uv run python evals/retrieval_gold/translate_meetings.py run \
    --model gemma4:26b-a4b --out $R --transcripts $T --meetings IS1004a
# 1b. The rest (GPU), same --out, so IS1004a is not redone.
uv run python evals/retrieval_gold/translate_meetings.py run \
    --model gemma4:26b-a4b --out $R --transcripts $T
# if windows failed: read `failures` in $R/manifest.json, then add --retry-failed

# 2. The owner reviews a seeded sample (no model): accurate / minor issue / wrong.
#    The owner fills in every Verdict line of the sheet.
uv run python evals/retrieval_gold/translate_meetings.py sample-sheet \
    --out ~/openkos-eval-runs/retrieval-gold/translation-review.md \
    --translations $R --transcripts $T --per-meeting 10 --seed 1609

# 3. Pin the hashes from the FILLED sheet (no model). Prints "k of N wrong" and refuses
#    above the bar.
uv run python evals/retrieval_gold/translate_meetings.py pin \
    --translations $R --transcripts $T \
    --sheet ~/openkos-eval-runs/retrieval-gold/translation-review.md

# 4a. Plan (no model).
uv run python evals/retrieval_gold/build_bundle.py plan --transcripts $T \
    --translations $R --prose-dir <checkout with the prose>
# 4b. Ingest pilot (GPU): ONE Spanish source. Then STOP: forecast the Spanish ingest
#     from its measured seconds per window, not from the English rate.
uv run python evals/retrieval_gold/build_bundle.py build --limit 1 --only ES2015a-es \
    --workspace ~/openkos-eval-runs/retrieval-gold/bundle-v1 --transcripts $T \
    --translations $R --prose-dir <checkout with the prose>
# 4c. The full build (GPU), same --workspace, so the pilot source is not redone.
uv run python evals/retrieval_gold/build_bundle.py build \
    --workspace ~/openkos-eval-runs/retrieval-gold/bundle-v1 --transcripts $T \
    --translations $R --prose-dir <checkout with the prose>
```

Until step 3 is done `build_bundle.py build` refuses to start and names the 12 pending
sources, and `plan` lists each as a problem. `pin` writes the reviewed files' sha256
and size into `bundle_sources.json`, with the model, the prompt and the review counts
(pairs, accurate, minor issue, wrong, sheet sha256); the change is committed like any
other source pin. A translation that is later edited by
hand, or re-run, no longer matches its pin, and `build` refuses it.

The sample sheet holds a seeded random draw of up to 10 aligned EN/ES turn pairs per
meeting (120 in all), taken among turns of 6 or more English words, so the owner does
not spend the review on `Okay.`

**Acceptance bar (the owner's):** the pin is blocked when **more than 10%** of the reviewed
pairs are `wrong`. With 120 pairs, 12 wrong passes and 13 blocks. `pin` reads the filled
sheet, so `--reviewed` no longer exists. It refuses a sheet with any pair unfilled or
marked with anything but `accurate`, `minor issue` or `wrong`; one that skips a meeting;
one inside the repository; and one whose English or Spanish no longer matches the files
on disk (another run, or an edited translation). It prints `k of N wrong` and, above
the bar, pins nothing. A blocked pin means re-translating (a new `--out`, possibly a
changed prompt) and a new sheet, not editing the verdicts.

### Translation GPU forecast

**About 1.3 hours** (range 1.25 to 1.5) with `gemma4:26b-a4b` on this machine's Ollama,
about **1.05 hours** (range 0.9 to 1.25) to ingest the 12 Spanish files, so about
**2.4 hours** on top of the 2.4 hours already forecast for the English bundle. Inputs
and assumptions, in order:

| Piece | Basis | Value |
| --- | --- | ---: |
| English text to translate | the 12 `bytes` in `bundle_sources.json` | 374,291 chars |
| Windows | the 25 meetings are 834,117 bytes and 224 windows (above); scaled | about 100 |
| Output tokens | chars x 1.18 (assumed: Spanish runs longer than English) x 0.277 to 0.341 tokens per char (the repo's measured ratios, `concept.py`) | 122k to 151k |
| Decode | 32.22 tokens per second, Ollama, `gemma4:26b-a4b` (`runtime_screen`, 12,288 context) | 3,800 to 4,700 s |
| Prompt tokens | chars x 0.341 plus about 450 per window for the system prompt | about 173k |
| Prefill | 613.7 tokens per second (same source) | about 280 s |
| Retries | assumed 10% extra (about 1 window in 10 retried once) | +10% |
| **Total** | | **about 4,800 s (1.34 h); range 4,500 to 5,450 s** |

Spanish ingest: this is a **placeholder, to be replaced by the ingest pilot**. About 119
windows (100 x 1.18, assuming the translation is 18% longer) at 31.4 s per window
(`qwen3:8b`, the English figure above, n = 40) is 3,720 s; embedding about 80 more
documents at 0.1 to 1.0 s and ingest bookkeeping add about 60 s. With the p90 of 37.5 s
per window it is 4,500 s; with 100 windows, 3,200 s. No stored run covers `qwen3:8b`
extracting from a Spanish meeting transcript, so the real figure comes from the
`--only ES2015a-es` pilot (step 4b): its seconds per window times the 12 meetings'
window count is the forecast to use.

Assumptions that can move these: the decode rate was measured on one short reply, not on
a 1,700-token translation, and a MoE model's rate can fall as the context fills; the
1.18 and 10% figures are guesses; no record exists of `qwen3:8b` extracting from Spanish
meeting transcripts (the 31.4 s is English), and a thermal state different from the
stored runs moves everything. The pilot measures the real ratio: `eval_count` and seconds
are recorded per window in the manifest, so the forecast should be redone from `IS1004a`
before the full run.

### Building it

```bash
# No model: verify every selected file exists and matches its pinned sha256.
uv run python evals/retrieval_gold/build_bundle.py plan \
    --transcripts ~/Dev/Projects/openkos-eval-runs/concept-screen/inputs/transcripts \
    --prose-dir ~/Dev/Projects/openkos/examples/extraction-corpus/sources

# GPU-bound: init a fresh workspace, `ingest --auto` each source, `reindex`, write the manifest.
# Refuses to start while any Spanish translation is still pending (see "Order of operations").
uv run python evals/retrieval_gold/build_bundle.py build \
    --workspace ~/openkos-eval-runs/retrieval-gold/bundle-v1 \
    --transcripts ~/Dev/Projects/openkos-eval-runs/concept-screen/inputs/transcripts \
    --translations ~/openkos-eval-runs/retrieval-gold/translations \
    --prose-dir ~/Dev/Projects/openkos/examples/extraction-corpus/sources
# --limit N ingests at most N pending sources and --only ID[,ID] restricts the build to
# those sources (a pilot); --no-synthetic drops the tier.
```

`--transcripts` is the `concept-screen/inputs/transcripts` layout (AMI is not committed).
`--translations` is the directory `translate_meetings.py` wrote.
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

About **2.4 hours** of wall-clock for the 49 English and prose sources (range 2.2 to
2.9 hours), about **2.1 hours** with `--no-synthetic`, with the shipped `qwen3:8b` at
default config. The 12 Spanish translations add about 1.05 hours of ingest (see "Translation
GPU forecast"), so a full build is about 3.5 hours; the table below is the English part.
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
| `build_bundle.py` | Builds the gold bundle with production `ingest`; `plan`, `build`, `--self-test`. Refuses a pending translation. |
| `translate_meetings.py` | Translates the 12 development meetings into Spanish; `run`, `sample-sheet`, `pin`, `--self-test`. |
| `bundle_sources.json` | The selected sources with their sha256, language, kind and tier, and what was excluded and why. |
| `questions.draft.json` | The draft question set; no judgments. |
