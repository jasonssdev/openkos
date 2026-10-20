# Pre-registration: the English-question arm of the Ask screen (#1581)

**Status: REGISTERED when this file is committed, before any scored model
call of the English arm.** A change after that commit is a new registration,
not an edit. Follows [`PREREGISTRATION.md`](PREREGISTRATION.md) (#1560, which
left English questions unmeasured) and [`PREREGISTRATION-1583.md`](
PREREGISTRATION-1583.md) (the language-neutral refusal, now the production
`answer/system`), and reuses their harness, bundles, cache and metrics.
Refs #1581, #1560, #1556. This is a **measurement**: it adopts nothing and
changes no production code, default or prompt.

**Stacking.** The branch stacks on #1583's branch (`fix/1583-language-neutral-refusal`),
not on `main`, so the English arm is measured under the `answer/system` that
will ship.

## Question

#1560 asked only Spanish questions: over the Spanish bundle (`es-es`), over its
English mirror (`es-en`) and over the bundle `openkos ingest` compiled from
English transcripts (`es-built`). An English question over an English bundle,
the plainest use of a compiled English base, was never measured.

- **Q1. Language penalty.** For each model and role, how much does asking in
  Spanish cost over the same English bundle: the per-question difference
  `es-en` minus `en-en`, and `es-built` minus `en-built`?
- **Q2. Does #1560's sufficiency choice hold in English?** On `en-en` and
  `en-built`, is `gemma4:26b-a4b`'s sufficiency score above the baseline's, as
  it was on the Spanish questions (+0.078 round 2, +0.096 confirmation)?
- **Q3. Does the shipped refusal follow an English question?** Share of
  unanswerable English questions answered in English (the #1583 rule asks for
  the question's language; it was measured on Spanish questions only).

## Items

The same 32 questions as #1560, translated one to one into English
(`crosslingual_corpus.EN_TRANSFERRED`, the 20 transferred questions, and
`EN_ANCHORED`, the 12 anchored ones; `EN_QUESTIONS` is their concatenation).
Labels, anchored indices, gold tokens and English evidence phrases are
unchanged: only the question text is new. `check_english_questions` verifies
mechanically that the tuples mirror the Spanish ones in length, that no
English question repeats another, and that no gold token occurs in the English
question of its item; the harness `--self-test` runs it.

| set | bundle | questions | items |
| --- | --- | --- | --- |
| `en-en` | `attribution_corpus.EN_DOCS`, the English mirror | English | 32 |
| `en-built` | the bundle `openkos ingest` compiled in #1560 (`built_bundle_sha256` `59b21054aed1…`, tree unchanged) | English | 32 |

The Spanish sides, `es-en` and `es-built`, are **not rerun**: they are read
from the generations #1560 and #1583 cached, which the production prompt
reproduces byte for byte (checked: the rendered #1583 treated prompt equals
`_SYSTEM_PROMPT`, so the cache keys of the answer call are identical).
Sufficiency rows come from #1560's control runs (that call does not see the
answer prompt); answer and citation rows from #1583's treated arm, i.e. the
shipping prompt. Retrieval and assembly of the English items run once through
the production `answer()` at `limit` 8 with `bge-m3`
(`--stage contexts --questions en`, no chat call), stored in `contexts-en.json`;
the Spanish `contexts.json` is untouched and its hash unchanged.

64 items per run (32 per set), 128 rows per model over two seeds.

## Models and runs

| model | role here | digest |
| --- | --- | --- |
| `qwen3:8b` | baseline | as registered in #1560 |
| `gemma4:26b-a4b` | the #1560 sufficiency choice; the strongest answer candidate | as registered in #1560 |

`qwen3:14b` and `qwen3.6:35b-a3b` are **not run**: the issue asks for the
baseline and the selected model, and #1583 reported `qwen3:14b` only as
non-gating. Seeds 1 and 2 (n = 2), the seeds the Spanish sides have for both
models; both calls (sufficiency and answer); `num_ctx` 12288, `num_predict`
8192, thinking off, model-default sampling, timeout 60 s (#1560's stage-0
value for both models); a failed call scores 0, never skipped. A digest that
differs at run time stops the run.

## Metrics

The #1560 role scores, unchanged: sufficiency (verdict right), answer (share of
gold tokens stated, anchored items), citation (`USED:` correct). Per model and
role the deciding statistic is the **paired per-question difference** Spanish
minus English over the same bundle, mean over the two seeds, with a 95%
percentile bootstrap over questions (10,000 resamples, seed 42). Also reported:
each set's absolute mean, the share of answers in the question's own language
(adjacent and grounded separately), and the English sets' failure rate and
`USED:` compliance. Every count prints `n of 32`.

## What each result would mean

There is no adoption rule: nothing here can change a default.

| result | reading |
| --- | --- |
| a penalty whose 95% CI is entirely below 0 | asking in Spanish costs something on that role and bundle; reported, and a follow-up issue only if it is large enough to matter to a user |
| a penalty whose CI includes 0 | no detectable penalty at 32 questions per set; the interval width states what could be missed |
| a penalty above 0 | English is the harder language for that role |
| Q2: `gemma4:26b-a4b` minus baseline on `en-en` sufficiency, lower bound above 0 | the #1560 choice holds in English |
| Q2: lower bound at or below 0 | the gain is not shown in English; recorded as a limit of the #1560 choice, with no change to what #1560 selected |
| Q2: upper bound below 0 | an English regression the Spanish screen could not see; a follow-up issue |
| Q3: `gemma4:26b-a4b` adjacent answered in English below 0.80 (the #1583 floor) | the language-neutral wording does not generalize to English questions; a follow-up issue |

Limits stated now. Thirty-two questions per set, ten of them adjacent and the
same ten in both bundles, so intervals are wide and the three-role comparison
is descriptive. The two bundles are small synthetic corpora, `en-built` is
weaker for every model (7 of 12 anchored questions have no document gold
there), and the translation is one author's: a different English phrasing could
move a retrieval rank. `en-en` and `es-en` share one bundle, so their
difference isolates the question's language, not the bundle's.

## Forecast and budget

Medians from #1560's round 1 (both calls): `qwen3:8b` 2.72 s + 4.75 s,
`gemma4:26b-a4b` 1.90 s + 2.83 s per item; the Spanish sides are cache hits and
cost no Ollama time.

| step | forecast |
| --- | --- |
| contexts, 2 sets, `bge-m3` only | 0.5 min |
| `qwen3:8b`, 2 runs x 64 items x 7.5 s, plus load | 17 min |
| `gemma4:26b-a4b`, 2 runs x 64 items x 4.7 s, plus load | 12 min |
| **total Ollama wall-clock** | **about 0.5 h, well under the 3 h cap** |

If measured seconds per item put the remainder over 3 h, the run is cut to one
seed before continuing and the cut recorded. One Ollama job at a time;
`OLLAMA_NUM_PARALLEL` at the server default; this run is the only user of the
server.

## Commands

```
uv run python -u evals/ask_model_screen/run_ask_model_screen.py --stage contexts \
    --questions en --built-bundle ~/Dev/Projects/openkos-eval-runs/ask-screen/built-workspace/bundle
uv run python -u evals/ask_model_screen/run_ask_model_screen.py --stage generate \
    --questions en --model <m> --runs 2 --timeout 60
uv run python -u evals/ask_model_screen/run_ask_model_screen.py --stage compare-language \
    --candidates qwen3:8b,gemma4:26b-a4b --arm refusal-lang --runs 2
```

Generations, projections and the comparison report stay outside the repository
under `~/Dev/Projects/openkos-eval-runs/ask-screen/`; the results file records
numbers, never a generated answer.
