# Pre-registration: choosing the Ask models (#1560)

**Status: REGISTERED on merge, 2026-10-19, before any scored model call.** A
change after the merge is a new registration, not an edit. Part of #1556,
step 3 of its agreed order. The harness and the design note are in this
directory (`README.md`). This file replaces `PREREGISTRATION-DRAFT.md` (#1566);
what the draft left open is closed below.

## Closed items

| # | Item | Registered value |
| --- | --- | --- |
| O1 | Extraction model and window for `es-built` | `qwen3:8b` (digest `500a1f067a9f…`), `_CHUNK_TARGET` 4000: the #1557 and #1558 verdicts ("no winner", both defaults stay). Built by `openkos ingest --auto` at openkos `340b1cb5` (see "Bundle provenance") |
| O2 | Top extraction model as a sixth candidate | **none**: #1557's registered rule named only `qwen3:8b`, already the baseline |
| O3 | Candidate digests | table below, from the local Ollama's `/api/tags` |
| O4 | Timeouts | the stage-0 rule below; the values are recorded in the results |
| O5 | D1 to D4 | decided below ("Decisions") |

## Question

Which local chat model should Ask use for each of its roles:

- **answer**: synthesis, `answer()` in `retrieval/answer.py`, reached from
  `api/_ask.py`;
- **sufficiency**: `_context_holds_the_answer`, the pre-synthesis check;
- **citation**: the `USED:` line closing the synthesis reply?

All three follow the global `model:` today (`qwen3:8b`). Citation shares the
synthesis call, so its model is necessarily the answer model; only sufficiency
can be split from it. This is a **screen plus a confirmation**. It adopts
nothing. A new task key in `TASK_MODEL_KEYS`, or any default, gets its own ADR
in a separate PR, and only if the rule below selects and confirms a model.

**Thesis link.** MCD-AFG requires one answering model for C1, C2 and C3 (its
§5.3). The answer-role result is the input to jasonssdev/mcd-afg#143.

## Candidates

The models already pulled on the measuring machine (Ollama 0.32.9), the
draft's list. All run with thinking off, model-default sampling, `num_ctx`
12288 and `num_predict` 8192 (the production defaults), and seed = run number.

| model | digest (`/api/tags`) | role in the screen |
| --- | --- | --- |
| `qwen3:8b` | `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41` | baseline |
| `gemma4:26b-a4b` | `001e5dafc3c77684c2307ebc6ab8e336e10c9b18eca52acf547d72fc83c3ca8c` | candidate (passed the #1269 sufficiency veto; never compared on quality) |
| `qwen3:14b` | `bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8` | candidate |
| `qwen3.6:27b` | `bcbdbd4bcf86f2c4a56cd1698fb31f9434f7dd99752f71b636c750aaa67a49a5` | candidate (dense) |
| `qwen3.6:35b-a3b` | `a7eb95c53bcf96b4bdd008d0fab4a5dac88047d9c1a7a9ab88ed453423fbd87c` | candidate (mixture of experts, about 3B active) |

Embedding model: `bge-m3` (`7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab`),
in the contexts stage only. `gpt-oss:20b` is not a candidate; it is not used.
Other pulled models (`mistral-small3.2:24b`, `gemma4:12b`, `phi4:14b`) are not
screened: the budget below has no room for them, and none has an Ask result
that would put it ahead of the listed ones. A digest that differs at run time
stops the screen for that model.

## Bundle provenance

| set | bundle | items |
| --- | --- | --- |
| `es-es` | `grounding_corpus.DOCS`, Spanish, 14 synthetic documents | 32 |
| `es-en` | `attribution_corpus.EN_DOCS`, its document-by-document English mirror | 32 |
| `es-built` | compiled by `openkos ingest` from the three English large-rung transcripts (`attribution_large_sources.LARGE_SOURCES_EN`) | 32 |

`es-built` was built before this registration, outside the repository under
`~/Dev/Projects/openkos-eval-runs/ask-screen/built-workspace/`, from three
files `<slug>.md` holding `# <title>` and the transcript. Its build and the
contexts stage made no chat call to a candidate and scored nothing; they are
recorded here so the registration fixes the items:

| fact | value |
| --- | --- |
| built at | 2026-10-19 13:07-13:14 EST, openkos `340b1cb5` (`origin/main`), `openkos init --model qwen3:8b --embedding-model bge-m3` then `openkos ingest <dir> --auto` |
| ingest result | 3 sources ingested, 0 extraction-degraded; 14 derived objects: 3 `Decision`, 1 `Project`, 10 `Person` (meetings 02 and 03 yielded only `Person` objects); each `Source` embeds its transcript verbatim |
| bundle | 19 `.md` files, 17 documents indexed besides `index.md` and `log.md` |
| `built_bundle_sha256` | `59b21054aed1ad11cc25a79fcb74f794a048dabf46c24d9029daaa6ccd3dce2f` |
| `corpus_sha256` (contexts) | `4c5f5149134ab25958651b1ce0fc7d950918a781911b69d4c6138dbb379ed6cf` |
| contexts | 96 items, none without context, 8 blocks each |
| anchored gold on `es-built` | 7 of 12 have an empty document gold (their evidence phrases are the small rung's wording, which the large transcripts and the extracted objects do not repeat); the other 5 reach the context. `es-es` and `es-en`: 12 of 12 reach it |
| wall-clock | ingest 420.5 s, contexts 19.5 s: 0.12 h of the budget |

No AMI material and no thesis series is used anywhere: every source is
synthetic and in the repository. The dev-series restriction of #1556 is
therefore met trivially.

## Items

The same 32 Spanish questions in every set (96 items per run):

- 20 transferred from `grounding_corpus` (10 grounded, 10 adjacent; labels
  carry over);
- 12 anchored grounded questions from `crosslingual_corpus`, whose gold is
  derived mechanically from each bundle's own bodies (see `README.md`).

On `es-built` an anchored item whose evidence phrase did not survive
extraction keeps its token gold for the answer role and has an empty document
gold (count above). Retrieval and assembly ran once, through the production
`answer()` at the production `limit` (8); every model is sent the stored,
byte-identical prompts.

### Multilingual coverage

The Spanish-question arm #1560 asks for is the design's own: `es-en` and
`es-built` are Spanish questions over English bundles, and `es-es` is the
same-language control. Each model's cross-lingual penalty (`es-en` minus
`es-es`, paired by question) is reported per role, and X1 vetoes a clear
cross-lingual loss. **English questions are not measured**: one more set is
+33% of every round, which does not fit the 4 h budget once the confirmation
is counted. That arm is #1581.

## Metrics per role

A failed call (a timeout or an error) scores **0**, never "skipped".

| role | primary (per item, mean over runs) | items |
| --- | --- | --- |
| sufficiency | verdict correct: grounded -> SUFFICIENT, adjacent -> `NONE` | all |
| answer | share of the derived gold tokens the answer states | anchored |
| citation | grounded: reported, non-empty `USED:`, naming a derived gold document when one reached the context; adjacent: `USED: none` | all |

The deciding statistic per role is the **paired delta** (candidate minus
`qwen3:8b`) of the per-item primary, pooled over the three sets (D3), with a
95% percentile bootstrap over items (10,000 resamples, seed 42). Per-set deltas
and each model's cross-lingual penalty are reported beside it.

## Vetoes (any one drops the candidate from that role)

| id | role | rule |
| --- | --- | --- |
| V1 | all | failure rate of the role's call <= baseline + 0.02 and <= 0.05 absolute |
| V2 | all | median latency of the role's call <= 2.0x the baseline's (the #1269 S4 budget; D2) |
| S1 | sufficiency | grounded refusal rate <= the baseline's |
| S2 | sufficiency | adjacent miss rate <= baseline + 0.02 |
| V3 | answer | share of answers whose dominant language is Spanish >= baseline - 0.05 |
| V4 | citation | `USED:` compliance (attribution `reported`) >= baseline - 0.05 |
| X1 | all | on `es-en`, the 95% upper bound of the per-set delta is not below 0 |
| M | all | 24 GB budget: the candidate at `num_ctx` 12288 plus `bge-m3` (0.66 GB, ADR-0047), the candidate measured as the sum of the buffer sizes `llama-server` reports at load in the Ollama server log (the ADR-0047 method, not `ollama ps`) |

## Protocol: successive halving

1. **Contexts.** `--stage contexts --built-bundle <bundle/>`: `bge-m3` only.
   Done before registration (above).
2. **Stage 0, calibration and gates (no quality decision).** Per model,
   `--stage generate --sample 5 --first-run 101 --runs 1 --timeout 600`: the 5
   evenly spaced `es-en` items, seed 101, outside every scored seed. Records
   seconds per call and memory (M). Fixes each model's timeout:
   `timeout = max(60 s, 4 x the model's stage-0 median answer call)`, used for
   both calls. A model whose stage-0 median answer call is above 2.0x the
   baseline's leaves answer+citation, and one whose median sufficiency call
   is above 2.0x leaves sufficiency (V2, D2); a model leaving every role, or
   failing M, enters no round. The disconnect check the draft listed is not
   rerun: #1557's stage 0 measured on this machine and server, on 2026-10-18,
   that an aborted HTTP call stops server-side generation.
3. **Round 1, n = 1** (seed 1). Every surviving candidate and the baseline,
   the calls of the roles it survived, every set. Per role: drop a candidate
   failing any veto; drop it when its 95% upper bound is below 0; of the rest
   keep the top `ceil(k/2)` by point delta, `k` being the number that
   entered, never fewer than 2 when 2 are eligible (`ask_screen_core.
   halving_cut`). A candidate continues only for the roles it survived
   (`--calls`).
4. **Round 2, n = 3** (seeds 2 and 3 added; seed 1 is reused from the cache),
   or n = 2 if the shortening rule says so. **Selection** per role: the
   largest point delta whose 95% lower bound is above 0, with every veto
   holding (`ask_screen_core.select`). If none, the baseline stays and the
   result is recorded as it is, not retried with tuned bars (#1269 rule 4).
5. **Confirmation**, only for a selected model: fresh seeds from 11
   (`--first-run 11`, n from the shortening rule, at most 10), the selected
   model and the baseline, the same rules. The screen's own runs are not
   reused, because a screen winner carries selection bias. "Confirmed" means
   `select` names the model again over the confirmation runs. A sign flip, a
   lower bound at or below 0, or a veto means "not confirmed".

Answer and citation share a call, so a model is selected for answer+citation
only if it is selected for both, or if it is selected for answer with citation
holding V4 and X1.

**Adoption.** Only a confirmed model is proposed for adoption, in a separate
PR with its own ADR (status Proposed). A confirmed sufficiency-only winner
needs a new task key (D4). Without a confirmed model nothing changes.

## Budget and the shortening rule

**Budget: 4.0 h of Ollama wall-clock** for the whole issue, counted from the
start of the `es-built` ingest to the end of the confirmation. The models run
one at a time, each through all its calls of a round before the next loads.

`ask_screen_core.shorten` is the rule. It walks `LADDER`, the pairs
(round-2 runs, confirmation runs) `(3, 10), (3, 5), (2, 5), (2, 3), (2, 0)`,
and takes the first whose forecast fits the hours left:

```
forecast = spent + rounds still ahead at their fixed size
         + (round-2 runs - 1) x one round-2 run (baseline + its candidates)
         + confirmation runs x one confirmation run (baseline + the slowest
           candidate still able to win)
```

with one run of a model = 96 items x its measured seconds per item (both
calls; one call for a candidate narrowed by `--calls`). The forecast is
redone, and the rule applied again, at three points: after stage 0 (with
stage-0 seconds and, for round 2, the two slowest surviving candidates),
after round 1 (with round-1 seconds and the actual survivors), and after round
2 (with the selected model). The rule only shortens: a rung once taken is the
floor for every later point. The rung `(2, 0)` means no confirmation; a
selection then stands as "selected, not confirmed", nothing is adopted, and a
follow-up issue carries the confirmation.

**Hard stop.** If the Ollama clock reaches 4.0 h while a stage is running, the
stage finishes its current model and stops. An unfinished round 2 makes no
selection; an unfinished confirmation is "not confirmed (budget)". Either is
recorded as such, with a follow-up issue.

### Forecast at registration

Inputs: `qwen3:8b` takes about 5.2 s per item for both calls (the draft's
figure from #1377's stored `query_limit` run), 8.3 min per 96-item run. Speed
ratios from #1557's stage 0 on this machine, extraction windows, are the best
local evidence: `gemma4:26b-a4b` 0.33, `qwen3:14b` 0.72, `qwen3.6:27b` 1.46,
`qwen3.6:35b-a3b` 0.20. The draft's decision-pass ratios are higher
(`gemma4:26b-a4b` 0.6-1.0, `qwen3:14b` 1.1, `qwen3.6:27b` 3.4,
`qwen3.6:35b-a3b` 0.6), and the larger of the two is used here. The Ollama
clock is the summed wall-clock of every stage command.

| step | forecast |
| --- | --- |
| build `es-built` + contexts (done) | 0.12 h |
| stage 0 (5 models x 5 items, plus loads) | 0.25 h |
| round 1, n = 1 (ratios 1 + 1.0 + 1.1 + 3.4 + 0.6) | 1.0 h |
| round 2 per added run (baseline + the two slowest, 1 + 3.4 + 1.1) | 0.76 h |
| confirmation per run (baseline + a 1.1x winner) | 0.29 h |

On these inputs the rule gives `(3, 10)` 5.8 h, `(3, 5)` 4.3 h and **`(2, 5)`
3.6 h, the rung it would take**. This forecast is informational: the first
binding application of the rule is after stage 0, with measured seconds, and
it may land on any rung. From then on a rung taken is the floor.

## Hardware budget

24 GB budget on the 32 GB floor; one chat model resident at a time (ADR-0047).
`bge-m3` is needed only in the contexts stage. `OLLAMA_NUM_PARALLEL` stays at
the server default: a default Ollama serializes, and raising it would change
the latency V2 gates on. One Ollama job at a time: before each stage no other
evaluation process may be using the server.

## What is stored where

Generations, the contexts file, the built workspace, the per-model run
projections and the comparison reports live outside the repository, under
`~/Dev/Projects/openkos-eval-runs/ask-screen/`, so a rescore is free. The
results file in this directory records the numbers, the digests and the stamp,
never a generated answer.

## Decisions (D1 to D4)

- **D1. Confirmation size:** from the shortening rule, at most n = 10 fresh
  seeds. At least 3 is needed for "confirmed"; at 0 the result is "selected,
  not confirmed".
- **D2. Latency budget:** kept at 2.0x for both calls, applied at stage 0 and
  in every round.
- **D3. `es-built`:** pooled into the deciding statistic, as drafted. Its
  per-set delta is reported.
- **D4. Sufficiency on its own model:** it may be selected separately from
  answer+citation; adopting it would need a new task key and its own ADR.
  The thesis needs only the answer role.
