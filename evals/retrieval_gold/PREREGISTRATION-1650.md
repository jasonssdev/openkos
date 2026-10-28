# Pre-registration: index the questions a Decision answers (#1650)

**Status: DRAFT. Registered when this file merges, before any measured run.** A
change after the merge is a new registration, not an edit. Part of #1652. Nothing
in this file has been run: no model was contacted, and the bundle and question set
it is measured on (the v1 set of #1609) do not exist yet. Items marked
**Owner confirms** carry a recommended default and are the only open choices.

## The question

A question and the Decision that answers it often share no vocabulary, so neither
BM25 nor the dense embedding of the Decision text bridges the gap (the downstream
example: the question says a feature was "considered outdated", the Decision says
"teletext"). Doc2query-style expansion: at extraction, generate three questions each
Decision answers, and index them with it. Does that raise retrieval on the questions
that have the gap, without costing the ones that do not?

The evidence for the gap is one example class from one downstream run (n = 14, one
series). So the registration measures first whether the gap EXISTS in the v1 questions
(Step 0) and only then whether the expansion closes it.

## Data

Development series only, from the repository: the v1 gold bundle built by the
production `ingest` (#1609), its v1 questions and adjudicated judgments (frozen,
identified by `questions_sha256`), and the passages file of #1646. No thesis
evaluation series, no held-out or reserve series, nothing from
`evals/decision_extraction/` TS3005, and never the 14 downstream questions of #1652.
Held-out confirmation is a separate registration the owner opens.

## Step 0: the vocabulary-gap stratum (model-free, frozen before any generation)

The stratum is a property of a question and its gold Decision text, blind to every
retrieval result and to the generator, so it can be fixed first.

- **Population:** v1 questions with at least one gold Decision (a judged grade 1 or 2
  document whose `type` is `Decision`). A question whose gold is only a Source or a Concept
  is outside it: expansion cannot help it by construction.
- **Coverage** of a question = the share of its content stems found in its best gold
  Decision (title, description, body). Content stems are lower-cased words of three or
  more letters, minus the repo's ES/EN function words (`LANGUAGE_FUNCTION_WORDS`), cut to
  five characters. Crude on purpose.
- **Stratum** = population questions with coverage **at most 0.5**.
  `prereg_tools.py gap-stratum` prints the ids and a sha256 of the sorted list; that hash
  is recorded in the results file before the generation command is run, and
  `stratum-compare` reads it back so no question can be added or dropped afterwards.
- **Floor:** at least **12** questions in the stratum. Below that the result is descriptive
  and cannot adopt anything. Whether v1 yields 12 is not known: its authors wrote questions
  against documents they had seen, which tends to copy vocabulary. If the floor is not met,
  the owner decides whether to author more paraphrase-style questions (see Owner confirms).

## The generator (fixed now)

- **Model:** `qwen3:8b`, the shipped ingest model, through the production `OllamaClient`
  (`think: False`, the production setting). Generated once per Decision; the output file
  is the artifact all arms share, keyed by the sha256 of the prompt and the Decision text,
  with the model digest recorded.
- **Input:** the Decision's title and stored body, and nothing else. No other Decision, no
  question, no judgment, no passage, no example. The generation command takes the bundle and
  no questions file, so the gold questions are not reachable from it. There are no few-shot
  examples on purpose: a model copies an example sentence into its output (#1583).
- **Prompt P1 (primary), verbatim.** System message:

```text
You write search questions for a personal knowledge base. You are given one decision that was recorded in it. Write 3 different questions that a person who has not read this decision might ask, and whose answer is stated in this decision. Write every question in the same language as the decision. Each question must be answerable from the decision text alone and must be 25 words or fewer. Reply with a JSON array of exactly 3 strings and nothing else.
```

  User message:

```text
Decision title: {title}

Decision:
{body}
```

- **Prompt P2 (exploratory), verbatim:** P1 with this sentence inserted before the last one:
  `Describe what the person wants to know; do not copy the distinctive words of the decision when an ordinary description of the topic would do.`
  It exists because the instruction is the mechanism one would expect to bridge a vocabulary
  gap, and a longer prompt has lost an A/B in this repo before; so it is measured, not assumed.
- **Output handling, deterministic:** the reply must parse as a JSON array of strings (the
  repo's fail-closed `extract_json_items`); an item is kept when it has 4 to 25 words, ends
  in `?`, and the function-word vote gives the Decision's language. Items within one Decision
  that share a Jaccard of 0.8 or more are collapsed. A Decision with fewer than two kept items
  gets none, and the count is reported. One retry with the same prompt on a failed parse, then
  the failure is recorded, never a second prompt.
- **Leak audit:** `prereg_tools.py leak-audit` compares every kept question with every gold
  question (Jaccard of word sets, bar 0.8). **More than 2% of generated questions being near
  copies of a gold question voids the run**, because the generator was never shown the gold
  questions and a copy then means a leak or a template. The audit runs before any metric.
- **Faithfulness spot check:** 30 generated questions drawn with seed 1650, each judged "answerable
  from the Decision text alone" yes or no. Bar: **at least 27 of 30**. A hallucinated question
  would pull a Decision into questions it does not answer.

## Where the questions are stored, and why

The experiment does not depend on this: the harness keeps the generated questions in a
`retrieval-gold-generated-questions` file outside the repository. The product decision,
taken only on adoption and recorded in an ADR, has three candidates:

| Option | For | Against |
| --- | --- | --- |
| **(a) Frontmatter key on the Decision** (for example `questions_answered:`), an OKF section 4.1 extension | Canonical, so a reindex rebuilds from files with no model call. Degrades gracefully: any OKF consumer ignores an unknown key, as it does `source_frontmatter` and the typed edges. Sensitivity, export, `forget` and merge already apply to the file it is in | Changes the Decision's content hash (one chunk, so a cheap re-embed); the questions are not knowledge and show up in the file; stale the moment the Decision text is revised unless regeneration is tied to the revision |
| (b) Sidecar under `bundle/.state/` | Keeps the concept file clean | A new tenant for the `forget` sweep and for the export decision (`docs/architecture.md` already warns that `.state/` ships with a zipped bundle); needs its own stale-detection |
| (c) Only in `vectors.db` / `fts.db` | No file change | Violates reconstructibility: generation is a model call, so `reindex` could not rebuild it from canonical files without spending inference per Decision |

Recommendation: **(a)**. Option (c) is rejected on the principle (every store under
`.openkos/` is a derived cache, rebuildable from markdown, `openkos.yaml` and git).

## How the questions enter retrieval

| Arm | Change |
| --- | --- |
| A0 baseline | The shipped pipeline |
| **A2 (primary)** | Each generated question is embedded and held in a SEPARATE vector table mapped to its Decision. A question-level search returns the Decisions nearest the user question (minimum distance per Decision, pool depth 10) and joins `fts` and `dense` as a THIRD list in reciprocal rank fusion (`rrf_fuse`, equal to `fusion.fuse` over two lists, tested), then the shipped `select_top` |
| A1 (exploratory) | The question vectors are extra rows for the Decision in the same `vectors` table, so the existing minimum-distance collapse picks them. No new list |
| A3 (exploratory) | The questions are appended to the Decision's text in the FTS index only. Lexical channel only |
| C1 (control) | A2 with every Decision's questions reassigned to a different Decision (a seeded derangement, seed 1650) |

C1 is the control the claim needs: it adds the same third list, the same embedding cost and
the same RRF disturbance, and none of the meaning. A2's gain counts only to the extent it
exceeds C1's; if C1 shows at least half of A2's gain on the primary metric, A2 is not credited
and the run is "no semantic effect". Questions and their embeddings are computed once and
shared by A1, A2, C1.

Known risk of A2: a third list always has ten Decisions in it, however poor the match, and RRF
will promote some. The guards below are how that shows up. No distance floor is added; that
would be a knob tuned on development data.

**Implementation, not built yet.** `rrf_fuse`, `gap-stratum`, `stratum-compare` and
`leak-audit` are built and tested. The `generate` command (the one GPU step), the
question-vector table, the FTS-field variant and the `run --doc2query` wiring are built under
TDD with the stub embedder and the stub LLM before the first run. `src/` does not change for
the experiment.

## Metrics, margins and the decision rule

Primary: **nDCG@5**, `fused`, on the frozen stratum, A2 minus A0, paired bootstrap
(`stratum-compare`, seed 1609, 10,000 resamples).

A2 is **adopted for implementation** only if all hold:

| # | Condition |
| --- | --- |
| P | Stratum nDCG@5 gain of at least **+0.05**, lower 95% bound above 0 |
| C | C1's gain is under half of A2's |
| G1 | Decision-gold questions outside the stratum: nDCG@5 falls by no more than 0.02 |
| G2 | All questions: nDCG@5 falls by no more than 0.01 (point estimate) |
| G3 | Questions with no gold Decision: recall@8 falls by no more than 0.03 (the third list spends slots on Decisions) |
| G4 | `delivered` at `fused@8` over the stratum does not fall (delta of at least 0 questions); `delivered_any` and the loss classes are reported |
| G5 | Leak audit passes, faithfulness at least 27 of 30, and G3 of #1648 (`judged@10` of the candidate at least 0.90, else re-pool) |
| K | The cost bars below hold |

Also reported: recall@5, recall@8 and MRR on the stratum, per language, and the same on A1,
A3 and P2. **A1, A3 and P2 are exploratory and cannot be adopted on their own result.**

Why +0.05: the stratum is small and the lift only exists where words differ, so a smaller
gain cannot be told from noise at the n this stratum will have; and a feature that adds an
LLM call per Decision should not be adopted for a rounding error.

**One shot.** Primary prompt P1, primary arm A2, one run. A positive estimate with a lower
bound at or below 0 is "inconclusive", which is not adoption. No tuned prompt, no second bar,
no extra question set after seeing a result.

## Ingest cost (priced before the run, measured on the dev bundle)

One additional chat call per Decision at ingest, and one per revised Decision. It falls under
the existing cost gates (the ingest estimate has to count it before it is shipped).

| Piece | Basis | Estimate |
| --- | --- | --- |
| Prompt | about 300 to 600 tokens (title, body, instructions) at `qwen3:8b` prefill, 299 tokens/s (`runtime_screen`) | 1 to 2 s |
| Reply | about 60 to 90 tokens at decode 31.2 tokens/s (same source) | 2 to 3 s |
| Per Decision | | **about 3 to 5 s** |
| Decisions in the v1 bundle | not known until it is built; `chunk-counts` reports the count by type with no model. Assumed 50 to 95 (15 to 30% of about 317 derived documents) | |
| Total | 50 to 95 Decisions at 3 to 5 s | **about 3 to 8 minutes of GPU** |
| Against ingest | the English and Spanish builds are about 3.5 hours | about 2 to 4% |

Same model as extraction, so no model swap. The harness generates from the stored Decision
text after the build, which is the same input the product call would see at ingest unless a
later merge rewrites the Decision (then the questions are regenerated).

**Bar K:** the measured p90 per-Decision generation time is at most **6 s**, and the
total is at most **10% of the bundle's recorded ingest wall time**. The run records seconds and
`eval_count` per call, so the forecast is replaced by the measurement. Reindex cost: each
question is one extra embed (about 3 per Decision, so 150 to 285 embeds, 15 to 285 s at the
unmeasured 0.1 to 1.0 s per call); Decisions are one chunk, so their own vector is unchanged.

## Threats, said now

- **Pool bias.** The judgments were pooled from the baseline channels. A2 can retrieve a
  relevant Decision no baseline channel retrieved and be scored as noise, which pushes against
  the candidate; G5 re-pools before the number is read.
- **The stratum is small.** Hence the floor, the larger margin and "inconclusive".
- **Questions written against the documents.** Authors who saw the Decision copy its words,
  so the gap may be thin in v1. Step 0 measures that, and a thin gap is a finding.
- **Interactions.** #1649 (Decision fidelity) changes Decision text and so the questions; the
  run is against the bundle as built for v1, and the questions are regenerated if it is
  rebuilt. #1647 (excerpts) acts after retrieval and does not change these rankings.
- **Spanish.** Spanish questions are generated for Spanish Decisions by a model whose Spanish
  question phrasing has no measurement in this repo; the faithfulness sample is stratified by
  language (at least 10 of the 30 from each).

## Harness status

| Piece | State |
| --- | --- |
| `gap-stratum`, `stratum-compare`, `leak-audit`, `rrf_fuse` | built, `--self-test` (this PR) |
| `generate` (P1, P2; the only GPU step) | **not built** |
| Question-vector table, A1, A3, C1 arms, `run --doc2query` | **not built** |

```bash
# no model: freeze the stratum, record the hash, then (and only then) generate
uv run python evals/retrieval_gold/prereg_tools.py gap-stratum \
    --bundle ~/openkos-eval-runs/retrieval-gold/bundle-v1/bundle \
    --judgments ~/openkos-eval-runs/retrieval-gold/judgments.json \
    --out ~/openkos-eval-runs/retrieval-gold/gap-stratum.json
# no model, after generation: voids the run above 2% near copies
uv run python evals/retrieval_gold/prereg_tools.py leak-audit \
    --generated ~/openkos-eval-runs/retrieval-gold/generated-questions.json
# no model: the primary comparison, on the frozen stratum
uv run python evals/retrieval_gold/prereg_tools.py stratum-compare \
    --stratum ~/openkos-eval-runs/retrieval-gold/gap-stratum.json \
    --base scores-a0.json --cand scores-a2.json --metric ndcg@5
```

## Owner confirms (recommended default first)

1. **Storage: (a) frontmatter key** on the Decision, decided by ADR on adoption. Alternative: (b)
   sidecar, if the questions should stay out of the concept file.
2. **If the stratum is under 12:** default is to report descriptively and stop, since the
   expansion cannot be shown to help a gap that v1 does not have. Alternative: author 15 to 20
   paraphrase questions about existing Decisions, written without reading the Decision text
   (from the Source passage only), before any generation.
3. **Faithfulness review:** default a model reviewer of a different family from `qwen3`, with a
   spot check by the owner of a random 10, as was done for the translations. Alternative: the
   owner reviews all 30.
4. **Margin: +0.05 nDCG@5 on the stratum.** Default as derived; lower it only if the stratum
   turns out large.
