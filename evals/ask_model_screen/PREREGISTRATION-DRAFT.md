# Pre-registration (DRAFT): choosing the Ask models (#1560)

**Status: DRAFT, 2026-10-18. Not registered, and nothing may be run against
it.** It becomes a registration only when the open items below are filled in
and the file is merged as `PREREGISTRATION.md` before the first timed run. A
change after that point is a new registration, not an edit.

Part of #1556, step 3 of its agreed order. The harness and the design note are
in this directory (`README.md`).

## Open items that block registration

| # | Item | Depends on |
| --- | --- | --- |
| O1 | The extraction model, and window size, that build the `es-built` bundle | #1557, #1558 |
| O2 | The top extraction model from #1557, as a sixth Ask candidate (placeholder below) | #1557 results on `main` |
| O3 | Digests of every candidate, from the local Ollama | stage 0 |
| O4 | Each model's timeout, from the stage-0 rule below | stage 0 |
| O5 | The owner's choice on D1 to D4 at the end of this file | the owner |

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
after the results.

**Thesis link.** MCD-AFG requires one answering model for C1, C2 and C3 (its
§5.3). The answer-role result is the input to jasonssdev/mcd-afg#143.

## Candidates

All are run with thinking off, model-default sampling, `num_ctx` 12288 and
`num_predict` 8192 (the production defaults), and seed = run number.

| model | role in the screen |
| --- | --- |
| `qwen3:8b` | baseline |
| `gemma4:26b-a4b` | candidate (passed the #1269 sufficiency veto; never compared on quality) |
| `qwen3:14b` | candidate |
| `qwen3.6:27b` | candidate (dense) |
| `qwen3.6:35b-a3b` | candidate (mixture of experts, about 3B active) |
| **TBD (O2)** | the top concept-extraction model from #1557, if it is not already listed |

`gpt-oss:20b` is not a candidate. It stays judge-only, and is used only if
`query_entailment` is run on finalists as an optional secondary.

## Bundle provenance

| set | bundle | status |
| --- | --- | --- |
| `es-es` | `grounding_corpus.DOCS`, Spanish, 14 synthetic documents | ready |
| `es-en` | `attribution_corpus.EN_DOCS`, its document-by-document English mirror | ready |
| `es-built` | **compiled by `openkos ingest`** from the three English large-rung transcripts (`attribution_large_sources`, `en`) with the extraction model and window chosen in #1557 and #1558 (O1), then indexed by this harness | **pending O1** |

The `es-built` workspace is built once, outside the repository, before stage 0.
Its `bundle/` tree is hashed (`built_bundle_sha256` in `contexts.json`), and the
extraction model, its digest and the openkos commit that built it are recorded
here at registration. No thesis evaluation or reserve series is used anywhere:
every source is synthetic.

## Items

The same 32 Spanish questions in every set (96 items with `es-built`):

- 20 transferred from `grounding_corpus` (10 grounded, 10 adjacent; labels
  carry over);
- 12 anchored grounded questions from `crosslingual_corpus`, whose gold is
  derived mechanically from each bundle's own bodies (see `README.md`).

On `es-built`, the anchored gold documents are derived over what extraction
wrote. An anchored item whose evidence phrase did not survive extraction keeps
its token gold for the answer role and has an empty document gold. That count
is reported.

Retrieval and assembly run once, through the production `answer()` at the
production `limit` (8), and every model is sent the stored, byte-identical
prompts.

## Metrics per role

A failed call (a timeout or an error) scores **0**, never "skipped".

| role | primary (per item, mean over runs) | items |
| --- | --- | --- |
| sufficiency | verdict correct: grounded -> SUFFICIENT, adjacent -> `NONE` | all |
| answer | share of the derived gold tokens the answer states | anchored |
| citation | grounded: reported, non-empty `USED:`, naming a derived gold document when one reached the context; adjacent: `USED: none` | all |

The deciding statistic per role is the **paired delta** (candidate minus
`qwen3:8b`) of the per-item primary, pooled over the sets, with a 95%
percentile bootstrap over items (10,000 resamples, seed 42). Per-set deltas and
each model's cross-lingual penalty (`es-en` minus `es-es`, paired by question)
are reported beside it.

## Vetoes (any one drops the candidate from that role)

| id | role | rule |
| --- | --- | --- |
| V1 | all | failure rate of the role's call <= baseline + 0.02 and <= 0.05 absolute |
| V2 | all | median latency of the role's call <= 2.0x the baseline's (the #1269 S4 budget) |
| S1 | sufficiency | grounded refusal rate <= the baseline's (the #1269 S1 veto; 0 when the baseline is 0) |
| S2 | sufficiency | adjacent miss rate <= baseline + 0.02 (the #1269 S2 veto, about 2 of 150) |
| V3 | answer | share of answers whose dominant language is Spanish >= baseline - 0.05 |
| V4 | citation | `USED:` compliance (attribution `reported`) >= baseline - 0.05 |
| X1 | all | on `es-en`, the 95% upper bound of the per-set delta is not below 0 (no clear cross-lingual loss) |
| M | all | 24 GB budget: the candidate plus `bge-m3` at `num_ctx` 12288, measured by the ADR-0047 method (the loader's buffer report, not `ollama ps`) |

## Protocol: successive halving

1. **Contexts.** `--stage contexts --built-bundle <...>`: `bge-m3` only.
2. **Stage 0, calibration and gates (no quality decision).** Per model, 5
   items, one run, outside the scored seeds: seconds per call, memory (M), and
   whether Ollama stops generating when the client disconnects (if it does
   not, a timeout would leave the next call queued behind it, and the timeout
   rule needs a server restart between models). Fixes each model's timeout:
   `timeout = max(60 s, 4 x the model's stage-0 median answer call)`.
3. **Round 1, n = 1** (seed 1). Every candidate and the baseline, both calls,
   every set. Per role: drop a candidate failing any veto; drop it when its
   95% upper bound is below 0; of the rest keep the top `ceil(k/2)` by point
   delta, `k` being the number that entered, never fewer than 2 when 2 are
   eligible (`ask_screen_core.halving_cut`). A candidate continues only for
   the roles it survived (`--calls`).
4. **Round 2, n = 3** (seeds 2 and 3 added; seed 1 is reused from the cache).
   **Selection** per role: the largest point delta whose 95% lower bound is
   above 0, with every veto holding (`ask_screen_core.select`). If none, the
   baseline stays and the result is recorded as it is, not retried with
   tuned bars (#1269 rule 4).
5. **Confirmation**, only for a selected model: fresh seeds 11 to 20
   (n = 10, D1), the selected model and the baseline, the same rules. The
   screen's own runs are not reused, because a screen winner carries selection
   bias. A sign flip, or a lower bound at or below 0, means "not confirmed".

Answer and citation share a call, so a model is selected for answer+citation
only if it is selected for both, or if it is selected for answer with citation
holding V4 and X1.

## Forecast

Inputs: `qwen3:8b` takes about 5.2 s per item for both calls: the median
non-refused `answer()` with the sufficiency check at `limit` 8 in #1377's
stored `query_limit` run (`runs-20261016-n15-qwen3-8b.json`, 239 answers), of
which about 1.1 s is the check (#760's quote arm median, 1.12 s). That is about
8.3 min per 96-item run. Speed ratios are #1557's decision-pass table:
`qwen3:14b` 1.1, `qwen3.6:27b` 3.4, `qwen3.6:35b-a3b` 0.6, `gemma4:26b-a4b`
0.6-1.0 (estimated). Model loads take about 1 min each.

| step | forecast |
| --- | --- |
| build `es-built` (ingest of 3 transcripts, chosen extraction model) | 10-20 min |
| contexts (`bge-m3`) | under 5 min |
| stage 0 (6 models x 5 items, plus loads and the disconnect check) | 15-25 min |
| round 1 (n = 1; 8.3 + 9.1 + 28.3 + 5.0 + 5.0-8.3 min, + up to 28 for O2) | 1.0-1.6 h |
| round 2 (baseline + up to 3 survivors per role, 2 more runs) | 0.9-1.8 h |
| confirmation (n = 10, winner + baseline) | 2.2 h (a 0.6x winner) to 6.1 h (a 3.4x winner) |
| **total** | **about 4.5-10 h**, most of it the confirmation |

`hours = sum over models of (items x runs x s_per_item x ratio) / 3600`, with
`ask_screen_core.forecast_hours` for each line. Every forecast is redone from
the stage-0 measurement before round 1.

## Hardware budget

24 GB budget on the 32 GB floor; one chat model resident at a time (ADR-0047).
The models run one at a time, each through all its rounds' calls before the
next is loaded. `bge-m3` is needed only in the contexts stage, so it never has
to co-reside with a 20 GB+ chat model during generation. `OLLAMA_NUM_PARALLEL`
stays at the server default: a default Ollama serializes, and raising it would
change the latency V2 gates on.

## What is stored where

Generations, the contexts file, the per-model run projections and the
comparison reports live outside the repository, under
`~/Dev/Projects/openkos-eval-runs/ask-screen/`, so a rescore is free. The
results file in this directory records the numbers, the digests and the stamp.

## Decisions for the owner (D1 to D4)

- **D1.** Confirmation size: n = 10 fresh seeds (the default here), n = 15 as
  in #1269 and ADR-0047 (+50% confirmation time), or n = 5 (half; on 96 paired
  items the item bootstrap carries most of the power, but this repo has seen a
  5-run arm swing wider than an effect).
- **D2.** Keep #1269's 2x latency budget (V2) for both calls? `qwen3.6:27b` at
  about 3.4x would fail it before any quality round, as in #1557's D1.
- **D3.** Is `es-built` required for selection, or reported only? This draft
  pools it into the deciding statistic.
- **D4.** May sufficiency be selected separately from answer+citation? That
  would need a new task key (ADR). The thesis needs only the answer role.
