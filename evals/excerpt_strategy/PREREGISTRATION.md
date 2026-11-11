# Pre-registration: excerpt strategy reach comparison (#1759)

**Status: PROPOSED. It becomes REGISTERED when the owner has confirmed the section
"The owner confirms" and this file has merged to `main`, before the held-out run.**
A change after that point is a new registration, not an edit to this one. It generates no
number itself: it fixes what will be generated, how it will be read, and what each outcome
means. Production code, prompts and defaults are unchanged by this file and by the run.

Items marked **(author's recommendation)** are design choices the owner may change before
registration; each carries a one-line rationale.

## Question

When a Source is longer than its share of the answer prompt, `_query_aware_excerpt`
(`src/openkos/prompt_budget.py`) keeps the best BM25-ranked windows of up to 800 characters
that fit. A diagnostic on 30 questions (issue #1759, exploration-grade, not registered)
found 20 misses at the shipped share and a grid in which smaller windows and a hybrid
ranking reached more. **Does any alternative excerpt strategy reach the evidence line
materially more often than the shipped one, on questions its author did not write against
the excerpt code?**

This is a model-free reach comparison. It answers nothing about whether the answer model
then answers correctly; that is a separate registration, owed only if this one supports an
arm (see "What a verdict does not establish").

## Data

- **36 answerable + 12 unanswerable held-out questions** about the **12 development-series
  meetings** ES2015a-d, TS3009a-d, IS1004a-d (AMI, CC BY 4.0), 3 answerable and 1
  unanswerable per meeting.
- **Written blind**: the author had no access to the diagnostic, the issue or its comments,
  the excerpt code, the gold decision files or any retrieval output, and ran no retrieval
  method while writing. Each answerable question has one verbatim transcript evidence line,
  copied programmatically by line number; each unanswerable control names a topic the
  meeting touches (`topic_regex`) and a value pattern that would answer it
  (`forbidden_regex`) that no line of the meeting matches.
- **Disclosed limits.**
  - These are the **same 12 meetings** as the diagnostic: held-out *questions*, not
    held-out *meetings*. `scoring.DEV_SERIES` reserves every other series for the thesis'
    final evaluation, and this harness refuses any meeting outside the three development
    series, so held-out meetings are not available to a tuning decision.
  - **One author.** Question style, difficulty and the choice of fact are one person's.
  - **Evidence is a single verbatim line** per question. A question whose answer is spread
    over several turns is represented by one of them; reach of that line is a lower bound on
    answerability, not answerability.
  - One corpus (task-oriented design meetings), one language (English), one embedding model.
- **The file is never committed.** It holds verbatim corpus lines, so it stays outside the
  repository at `~/Dev/Projects/openkos-eval-runs/heldout-questions-1759/questions.json`.
  The harness takes it by `--questions PATH` and **refuses unless its sha256 is exactly**
  `beac8f024ded10c59dab741f47d862e3a29ec6580fe4aae98e6dc36d8ecc758c`
  (`REGISTERED_QUESTIONS_SHA256`). The harness never reads the file's `answer` field.
- Before any arm runs, the harness checks the data, not a reach: the registered counts, every
  meeting in the registered 12, ids unique, every answerable evidence line occurring exactly
  once in its meeting's transcript. A failed check refuses the run.
- The rendered transcripts are read from the private thesis checkout
  (`~/Dev/Learning/UC/mcd-afg/data/interim/transcripts/<meeting>.md`); nothing from it is
  copied into this repository, and the run's stored output holds question ids and booleans,
  no transcript or question text.

## Readout

**Oracle reach**: with the right Source already chosen (this comparison does not test Source
selection), the excerpt the arm produces for the question, at a given per-Source character
share, **contains the evidence line**. Containment is verbatim and whole: the stripped
evidence line is a substring of the excerpt. This is the containment of the long Source
harness (`evals/source_fallback/measure_source_fallback_long.py`, `_holds_fact`) specialised
to evidence that is a transcript line rather than a gold act: that harness needs a
token-overlap fallback because gold acts are not transcript lines; here the verbatim branch
is exact, a window cut mid-line does not count, and uniqueness of the line in its meeting
(checked above) means no other line can match.

- **Primary share: 2179 characters** (the shipped per-Source share).
- **Secondary share: 2906** (the Source fallback's share). Reported, not decisive.
- Reach is counted over the 36 answerable questions, **n of 36**.

## Arms

All arms use the same char budget and the engine's own accounting (a window's length, plus 1
character across an adjacent pair and `len(marker)` across a gap), apply to the one Source,
and return the whole Source when it fits.

| Arm | Windows | Ranking | Marker | New derived state |
|---|---|---|---|---|
| `shipped` | 800 | BM25 | the engine's | none |
| `bm25-400` | 400 | BM25 | the engine's | none |
| `bm25-200` | 200 | BM25 | the engine's | none |
| `hybrid-400` | 400 | RRF (k = 60) of BM25 and bge-m3 cosine | the engine's | chunk-embedding cache |
| `hybrid-200` | 200 | RRF (k = 60) of BM25 and bge-m3 cosine | the engine's | chunk-embedding cache |
| `bm25-400-lean` | 400 | BM25 | **`"\n[...]\n"`** (7 characters) | none |

- `shipped` is the engine function itself: `prompt_budget.bounded_text` with the 800-character
  `chunk_lines` windows, `CONTEXT_ELISION_MARKER` and the question as query, the share taken
  from `fair_shares` as the answer path does. The BM25 arms call the same `bounded_text` with
  their own window and marker. The hybrid arms rank by reciprocal rank fusion
  (`1/(60+rank_bm25) + 1/(60+rank_embedding)`, ties to the earlier window) and select with a
  greedy packer that the self-test pins **equal to the engine's selection under BM25 scores**.
- Embedding: `bge-m3:latest` through the local Ollama, cosine of the question against each
  window, no prefix; the model digest is recorded in the store.
- **The lean marker is `"\n[...]\n"` (author's recommendation)**: the engine's marker is 51
  characters (the diagnostic text says 52; its numbers used `len(marker)`, so they are
  unaffected), a fifth of the 200-character-window budget at ten gaps, and a shorter marker
  is the cheapest way to test whether marker overhead alone costs reach. It changes the
  marker an answer model sees; whether that matters is not measured here.
- The hybrid arms use the engine's marker so that they differ from their BM25 twin by the
  ranking alone.

## Bars (author's recommendation)

All counts are at the primary share over the 36 answerable questions.

1. **An arm PASSES** if its reach is **at least the shipped reach + 6** and its **paired
   losses against shipped are at most 2** (questions the shipped arm reaches and the arm
   does not). Rationale: the diagnostic's best no-model arm gained 6 of 30 with 0 to 1
   losses; +6 of 36 is the same absolute gain on a larger set, and two losses tolerates
   noise without admitting an arm that trades one miss for another.
2. **Embedding justification.** A hybrid arm is eligible only if it beats the BM25 arm of
   the **same window** by **at least 3** at the primary share; then it is preferred over that
   BM25 arm. Otherwise the recommendation is the BM25 arm (no new derived state). A hybrid
   arm whose BM25 twin does not pass must still clear the 3. Rationale: embeddings need a
   persisted derived cache (the diagnostic measured about 4 to 5 s per 35 KB Source at index
   time), so they must earn it; 3 of 36 is the old fixture's hybrid-over-BM25 margin at 400
   (3) and below its margin at 200 (4).
3. **If no arm passes: NOT SUPPORTED; keep shipped.**
4. **Choice among passing, eligible arms**, in this registered order: **fewest new derived
   state** (the BM25 arms, including the lean one, add none; the hybrid arms add a cache),
   then **highest reach**, then the order of the arm table above. Rationale: the cheapest
   sufficient change wins; reach only breaks ties.
5. **Safety readout (reported, gating).** For each of the 12 unanswerable controls, count the
   lines of the arm's excerpt (markers excluded) that match the control's `topic_regex`, at
   the primary share, over the control's own meeting; sum over the 12. An arm whose total
   **exceeds 2x the shipped arm's total** is flagged **"premise-bait risk"** and **cannot be
   the recommendation without a model-side safety measurement**. A passing arm that is only
   flagged yields the verdict **SAFETY-GATED**, naming the arm, which carries it to the
   answer-quality registration as a candidate rather than a recommendation. Rationale: the
   diagnostic saw stronger arms pull about 1.5 to 2x more on-topic text on unanswerable
   questions, and on-topic text without a value is what lures a false answer. The harness
   also counts the lines matching `forbidden_regex` in every excerpt; any non-zero count
   means the control is invalid and is reported as such.

Verdict values: **SUPPORTED** (an arm named), **SAFETY-GATED** (an arm named, not
recommended), **NOT SUPPORTED** (keep shipped). The report applies these mechanically.

## Determinism

The BM25 arms and the shipped arm are deterministic. The embedding arms run **once** with
bge-m3 (the model digest is recorded). A second run is allowed for the check: the BM25 arms
must match exactly and each hybrid arm must match within **1 question** at either share
(`report --rerun`); otherwise the difference is disclosed in the results note. The first
store is the result; a rerun never replaces it.

## Disclosed prior: the diagnostic on the old 30-question fixture

The arms and bars above come from this prior, which was written by the author of the excerpt
rule, on the same 12 meetings (issue #1759; not a registered result). Oracle reach at
(2179, 2906), n of 30:

| Arm | Reach | Paired gain / loss vs shipped @2179 |
|---|---|---|
| shipped | 10, 14 | 0 / 0 |
| bm25-400 | 15, 19 | 6 / 1 |
| bm25-200 | 16, 17 | 6 / 0 |
| hybrid-400 | 18, 20 | 9 / 1 |
| hybrid-200 | 20, 21 | 10 / 0 |

Of its 20 shipped misses, 7 were paraphrase, 13 were outranked (8 narrowly), none were split
across turns. The arms that won are therefore the ones the prior favours; the held-out set
exists to find out whether that survives questions the rule's author did not write.

### Pre-run check (not held-out data): the harness reproduces the prior

`measure_excerpt_strategy.py fixture-old` runs the six arms on the old 30-question fixture
through the long harness's own loaders and containment rule (`_holds_fact`), which is data
the diagnostic already used and not part of this comparison. Observed before this file was
written, with both the diagnostic's cached bge-m3 vectors and freshly computed ones (bge-m3
digest `7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab`):

| Arm | @2179, @2906 | Diagnostic | Gain / loss @2179 |
|---|---|---|---|
| shipped | 10, 14 | 10, 14 | 0 / 0 |
| bm25-400 | 15, 19 | 15, 19 | 6 / 1 |
| bm25-200 | 16, 17 | 16, 17 | 6 / 0 |
| hybrid-400 | 18, 20 | 18, 20 | 9 / 1 |
| hybrid-200 | 20, 21 | 20, 21 | 10 / 0 |
| bm25-400-lean | 15, 18 | (none) | 6 / 1 |

Every diagnostic cell reproduces, and the shipped arm's text equals the engine's
`bound_sources` output for all 60 (question, share) cells. The lean arm, which the diagnostic
did not run, is 15 at 2179 and 18 at 2906 on that fixture.

**No arm's reach on the held-out questions was computed before this registration.** The
harness has been run only on its synthetic self-test data and on the old fixture above.

## Exact commands

The harness code is the registering PR's merge commit with a clean tree (the run refuses a
dirty tracked tree). `T` is the directory holding the rendered transcripts `<meeting>.md`
in the thesis checkout; `Q` is the questions file; `OUT` is outside the repository. Record
`git rev-parse HEAD` and `ollama list` first.

```sh
M=evals/excerpt_strategy/measure_excerpt_strategy.py
T=~/Dev/Learning/UC/mcd-afg/data/interim/transcripts
Q=~/Dev/Projects/openkos-eval-runs/heldout-questions-1759/questions.json
OUT=~/Dev/Projects/openkos-eval-runs/excerpt-strategy

# model-free, anywhere
OLLAMA_HOST=127.0.0.1:1 uv run python $M --self-test

# pre-run check on the OLD fixture (needs bge-m3 for the two hybrid arms)
uv run python $M fixture-old --transcripts $T

# the run: refuses unless sha256($Q) is the registered one
uv run python -u $M run --questions $Q --transcripts $T --out $OUT

# model-free from here: reach, paired gain/loss, safety readout and the verdict
OLLAMA_HOST=127.0.0.1:1 uv run python $M report $OUT/excerpts-<stamp>.json

# optional determinism check: a second run into its own file, then compare
uv run python -u $M run --questions $Q --transcripts $T --out $OUT/rerun
OLLAMA_HOST=127.0.0.1:1 uv run python $M report $OUT/excerpts-<stamp>.json \
    --rerun $OUT/rerun/excerpts-<stamp2>.json
```

- The run writes **one final store** per invocation, `excerpts-<UTC stamp>.json`, and no
  checkpoint file (it makes no model-generation call and takes a minute or two), so the
  `-PARTIAL` double-count of #1754 cannot arise. `report` reads **only the final store**,
  and refuses (non-zero exit, `REFUSED`) a store with a duplicate (arm, share, id), a missing
  cell, or a questions hash other than the registered one.
- The store holds, per cell, the arm, share, question id and a boolean, per control the
  counts of on-topic and forbidden lines, and the identity stamp (harness commit, bge-m3
  digest). It holds no transcript text, question text, evidence or gold.
- The embedding cache `emb-cache.json` is written under `OUT`; it holds vectors keyed by the
  sha1 of the text and is a derived cache, reconstructible.

## Stopping rules and deviations

- No early stop on a result. The only stop is a crash (re-run). There is no time ceiling:
  the run is model-free apart from a few thousand short embeddings.
- A deviation (a re-run, a harness fix that does not change a registered quantity) is listed
  in the results note with its reason. A change to the arms, the shares, the bars, the
  decision order or the questions file is a new registration.
- The results note applies this file mechanically. It may add readings, not verdicts.

## What a verdict does not establish

That the answer model answers better, or worse, with a fuller or more fragmented excerpt: a
200-character-window arm keeps about ten separated fragments where shipped keeps three, a
prompt shape the model has not been measured on. That an arm does not raise the false-answer
rate on unanswerable questions (the safety readout counts on-topic lines, not answers). That
Source selection, other series, other languages, other embedding models, or a real user's
Sources behave as these 12 meetings do. That the indexing cost of an embedding arm is
acceptable. A SUPPORTED or SAFETY-GATED verdict opens a registration for the answer-quality
run on the GPU, with the false-answer rate on unanswerable controls as a safety bar; it does
not change a production default.

## The owner confirms

1. **Data**: 36 + 12 questions, the 12 development meetings (held-out questions, not
   meetings), one author, the questions file with the registered sha256, kept outside the
   repository.
2. **Readout**: oracle reach by verbatim evidence-line containment; primary share 2179,
   secondary 2906.
3. **Arms**: the six listed, including the lean marker `"\n[...]\n"`.
4. **Bars**: PASS at +6 of 36 with at most 2 paired losses; hybrid eligible only at +3 over
   its BM25 twin; choice by fewest new state, then highest reach, then table order; premise-
   bait flag above 2x shipped's on-topic line count gates the recommendation.
5. **Determinism**: embedding arms run once, rerun within 1 question or disclosed.
