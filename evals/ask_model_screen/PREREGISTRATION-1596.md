# Pre-registration: the answer-role re-screen under the shipped prompt (#1596)

**Status: REGISTERED when this file is committed, before any new scored model
call.** A change after that commit is a new registration, not an edit. Follows
[`PREREGISTRATION.md`](PREREGISTRATION.md) (#1560, whose [`RESULTS.md`](RESULTS.md)
selected no model for the answer role because every candidate failed V3),
[`PREREGISTRATION-1583.md`](PREREGISTRATION-1583.md) / [`RESULTS-1583.md`](RESULTS-1583.md)
(the language-neutral refusal, now the shipped `answer/system`, which measured
the prompt and explicitly did not re-decide the answer role) and
[`PREREGISTRATION-1581.md`](PREREGISTRATION-1581.md) / [`RESULTS-1581.md`](RESULTS-1581.md)
(English questions). Refs #1596, #1560, #1583, #1581, #1556. This is a
**measurement**: it adopts nothing and changes no production code, default,
prompt or task key.

## Question

Under the shipped `answer/system` prompt (the language-neutral refusal), can
any candidate be **selected for the answer role** (answer + citation, which
share one call) against the `qwen3:8b` baseline, by #1560's own rule?

**Thesis link.** MCD-AFG requires one answering model for C1, C2 and C3 (its
§5.3); this is the input to jasonssdev/mcd-afg#143 that #1560 could not give.

## What is unchanged from #1560

Everything that decides a model, verbatim from `PREREGISTRATION.md`:

- **Items.** The same 96 items (32 Spanish questions over `es-es`, `es-en`,
  `es-built`; 36 anchored), the stored `contexts.json`, byte-identical prompts.
- **Metrics.** Answer: share of the derived gold tokens the answer states,
  anchored items. Citation: the `USED:` score. A failed call scores 0, never
  skipped. The deciding statistic is the paired delta (candidate minus
  `qwen3:8b`) of the per-item mean over runs, pooled over the three sets, 95%
  percentile bootstrap over items (10,000 resamples, seed 42).
- **Vetoes** (any one drops the candidate from the role): V1 failure rate of
  the answer call <= baseline + 0.02 and <= 0.05 absolute; V2 median answer
  latency <= 2.0x the baseline's; V3 share of answers whose dominant language
  is Spanish >= baseline - 0.05 (all answers, adjacent included); V4 `USED:`
  compliance >= baseline - 0.05 (citation role); X1 on `es-en` the 95% upper
  bound of the per-set delta is not below 0; M the 24 GB budget.
- **Selection** (`ask_screen_core.select`): the largest point delta whose 95%
  lower bound is above 0, with every veto holding; otherwise the baseline
  stays and the result is recorded as it is, not retried with tuned bars
  (#1269 rule 4). A model is selected for answer+citation only if it is
  selected for both roles, or selected for answer with citation holding V4 and
  X1.
- **Confirmation.** Only for a selected model, on fresh seeds from 11, the
  selected model and the baseline, the same rules; "confirmed" means `select`
  names the model again. A sign flip, a lower bound at or below 0 or a veto
  means "not confirmed". The screen's own runs are not reused.
- **Settings.** Thinking off, model-default sampling, `num_ctx` 12288,
  `num_predict` 8192, seed = run number, timeout 60 s (the #1560 stage-0 value
  for every model), digests as registered in #1560; a digest that differs at
  run time stops the run for that model.

## What differs, and why

Each row is a registered deviation from #1560's procedure, not a change of its
criteria.

| # | difference | reason |
| --- | --- | --- |
| P1 | The prompt is the shipped `answer/system` (the #1583 sentence). | It is the point of the question. `candidate-answer-system-1583.md` and `src/openkos/prompts/answer/system.md` are byte-identical (checked: `cmp`, 1,353 bytes). |
| P2 | **Successive halving is dropped**: every candidate runs seeds 1 to 3 (n = 3) and `select` is applied to the pooled n = 3. | Halving exists to spend a 4 h budget (#1560). Here the answer call alone is cheap and seeds 1 and 2 are cached for three models, so the whole round fits in under an hour (forecast below). It only keeps more candidates alive; it cannot select a model the halving would have dropped, because `select` still requires every veto and a lower bound above 0. |
| P3 | Only the **answer call** is made (`--calls answer`). | The role under test; the sufficiency role is settled (ADR-0076) and its call does not see this prompt. |
| P4 | **Stage 0 is not rerun**: the timeout (60 s) and the memory gate M are taken from #1560's stage 0. | The prompt change does not alter weights, context size or speed class. M stays as measured: `qwen3:8b` 6.35 GiB, `gemma4:26b-a4b` 17.76 GiB, `qwen3:14b` 10.38 GiB, `qwen3.6:35b-a3b` 21.10 GiB at `num_ctx` 12288, each plus `bge-m3` 0.66 GB, all inside 24 GB. |
| P5 | `qwen3.6:27b` is **not rerun**. | It failed V2 on both calls at #1560 stage 0 (answer 2.54x; a prompt change does not change its speed) and left every role. |
| P6 | **English questions are not rerun.** | #1581 already measured `qwen3:8b` and `gemma4:26b-a4b` under this prompt (no detectable language penalty). `qwen3:14b` and `qwen3.6:35b-a3b` have no English arm; if one is selected, its English-question behaviour is named as unmeasured in the verdict. |

## Candidates

| model | digest (`/api/tags`, checked at run time) | role |
| --- | --- | --- |
| `qwen3:8b` | `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41` | baseline |
| `gemma4:26b-a4b` | `001e5dafc3c77684c2307ebc6ab8e336e10c9b18eca52acf547d72fc83c3ca8c` | candidate (already adopted for sufficiency, ADR-0076) |
| `qwen3:14b` | `bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8` | candidate |
| `qwen3.6:35b-a3b` | `a7eb95c53bcf96b4bdd008d0fab4a5dac88047d9c1a7a9ab88ed453423fbd87c` | candidate |

No model is pulled for this work; all four are installed.

## What is reused from cache, and what is generated

The generation cache key includes the exact messages, so a hit proves the
prompt was byte-identical. The #1583 treated arm used exactly the shipped
prompt, so its answer generations are valid here (the rendered prompt equals
`_SYSTEM_PROMPT`, the check #1581 recorded). Verified before this commit by an
`--offline` replay (no model call): seeds 1 and 2, answer call, **192 of 192
cache hits and 0 misses** for each of `qwen3:8b`, `gemma4:26b-a4b` and
`qwen3:14b`. Nothing was scored or compared before this commit.

| model | seeds 1 and 2 | seed 3 | confirmation seeds |
| --- | --- | --- | --- |
| `qwen3:8b` | cached (#1583) | generate | generate (only if a model is selected) |
| `gemma4:26b-a4b` | cached (#1583) | generate | generate (only if selected) |
| `qwen3:14b` | cached (#1583) | generate | generate (only if selected) |
| `qwen3.6:35b-a3b` | **generate** (control-prompt answers exist for seed 1 only, and they are the old prompt) | generate | generate (only if selected) |

The cached rows keep the latencies recorded when they were generated (V2
medians pool all three seeds).

## Design

- **Round (n = 3, seeds 1 to 3).** 96 items per run, answer call only, four
  models, then `compare` over runs 1 to 3: paired deltas, vetoes, `select`, per
  set deltas, the cross-lingual penalty (answer and citation), guardrails.
- **Confirmation.** Only if the round selects a model for answer+citation:
  seeds 11 to 15 (`--first-run 11 --runs 5`), answer call only, the selected
  model and the baseline.
- **Outcomes.** (a) *selected and confirmed*: proposed for adoption in a
  separate PR with its own ADR and a config change, never in this branch;
  (b) *selected, not confirmed*; (c) *no winner*: `qwen3:8b` stays as the
  answering model. In every case this branch changes no default.
- **Secondary, not deciding.** Reported beside the verdict: each veto's
  outcome per candidate, citation-role delta, per-set deltas, the answer
  language share on adjacent and grounded questions, and absolute means.

### Limits stated now

The answer role rests on 36 anchored items per run (12 per set). #1583 already
reported, from the cached seeds 1 and 2, that `gemma4:26b-a4b` and `qwen3:8b`
have the same answer recall (0.833) under this prompt and `qwen3:14b` 0.750; so
the **expected outcome is "no winner"** for lack of a lower bound above 0, not
for lack of capability. This registration does not move the bar to make a tie
selectable: the rule is `select` as written, and a non-inferior but not better
candidate is reported as such with its vetoes, not selected. Whether non-
inferiority plus an existing resident model would justify adopting a model is a
decision for an ADR, not for this screen. The bundles are small synthetic
corpora, `es-built` is weak for every model, and the answer metric checks
named facts, not prose.

## Forecast and budget

**Budget: 4.0 h of Ollama wall-clock** from the first new call. Answer-call
medians from #1560 round 1: `qwen3:8b` 4.75 s, `gemma4:26b-a4b` 2.83 s,
`qwen3:14b` 6.57 s, `qwen3.6:35b-a3b` 3.22 s per item; 96 items per run; about
1 min per model load.

| step | forecast |
| --- | --- |
| `qwen3.6:35b-a3b`, seeds 1 and 2 (192 calls x 3.22 s) | 11 min |
| seed 3, four models (96 x (4.75 + 2.83 + 6.57 + 3.22) s), plus four loads | 32 min |
| **round total** | **about 0.7 h** |
| confirmation if `gemma4:26b-a4b` is selected (5 runs x 96 x (4.75 + 2.83) s), plus loads | 62 min |
| confirmation worst case, `qwen3:14b` selected (5 x 96 x (4.75 + 6.57) s) | 91 min |
| **worst case total** | **about 2.3 h, under the 4.0 h cap** |

All candidates are within budget, so none is cut. **Shortening rule** (the only
one): if after the round the forecast confirmation (n = 5, measured seconds
per item of the baseline and the selected model) would take the total past 4.0
h, the confirmation is n = 3 (the minimum for "confirmed"); if that still does
not fit, none runs and the verdict is "selected, not confirmed" with a
follow-up issue. **Hard stop:** if the Ollama clock reaches 4.0 h while a stage
runs, the stage finishes its current model and stops; an unfinished round makes
no selection and an unfinished confirmation is "not confirmed (budget)".

One Ollama job at a time; the models run one at a time; `OLLAMA_NUM_PARALLEL`
at the server default; this work is the only user of the server.

## Commands

The cache directory is a copy of #1560's (`generations/`, `contexts.json`,
`contexts-en.json`), so the old projections are not overwritten:

```
C=~/Dev/Projects/openkos-eval-runs/ask-screen-1596
uv run python -u evals/ask_model_screen/run_ask_model_screen.py --stage generate \
    --model <m> --runs 3 --calls answer --timeout 60 --cache-dir $C
uv run python -u evals/ask_model_screen/run_ask_model_screen.py --stage compare \
    --baseline qwen3:8b --candidates gemma4:26b-a4b,qwen3:14b,qwen3.6:35b-a3b \
    --runs 3 --cache-dir $C
# confirmation, only for a selected model:
uv run python -u evals/ask_model_screen/run_ask_model_screen.py --stage generate \
    --model <m> --first-run 11 --runs 5 --calls answer --timeout 60 --cache-dir $C
```

The harness is not changed. Generations, projections and comparison reports
stay outside the repository; the results file records numbers, never a
generated answer.

## What adoption would need (stated now, not done here)

Even a confirmed winner changes nothing in this branch. Adoption would be a
separate PR: an ADR in the style of ADR-0076 (status Proposed), a decision
whether answer/citation gets its own task key (today synthesis rides the global
`model:`; only sufficiency has a key) or the global `model:` changes, the
32 GB-floor memory consequence of two resident chat models (ADR-0047), and an
end-to-end latency check like ADR-0076's adoption bar.
