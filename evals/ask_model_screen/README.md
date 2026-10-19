# Ask model screen (#1560): design note

Which chat model should Ask use for each of its roles? This directory holds the
harness that measures it and the draft pre-registration
(`PREREGISTRATION-DRAFT.md`). Nothing here has been run. No default, prompt or
`TASK_MODEL_KEYS` entry changes because of it; a new task key would need its
own ADR, decided after the results.

```
uv run python -u evals/ask_model_screen/run_ask_model_screen.py --self-test
uv run python -u evals/ask_model_screen/run_ask_model_screen.py --stage contexts [--built-bundle <bundle/>]
uv run python -u evals/ask_model_screen/run_ask_model_screen.py --stage generate --model qwen3:8b --runs 1 --timeout <s>
uv run python -u evals/ask_model_screen/run_ask_model_screen.py --stage compare --baseline qwen3:8b --candidates <a,b,...> --runs 1
```

## Three roles, two calls

Production sends one model two calls per ask:

| role | where | call |
| --- | --- | --- |
| sufficiency | `_context_holds_the_answer` (`retrieval/answer.py`) | system prompt `answer/sufficiency`, before synthesis |
| answer | `answer()` synthesis, reached from `api/_ask.py` | system prompt `answer/system` |
| citation | the `USED:` line that closes the synthesis reply | none of its own |

Citation is not a third call, so a per-role model can only ever split
**sufficiency** from **answer + citation**. The screen scores citation as its own
role, but its model is necessarily the answer model.

The screen calls synthesis for every item whatever the sufficiency verdict, so
the answer role is scored on its own and not through another model's gate. The
shipped composition (refuse when the check says `NONE`) is derivable from the
same rows.

## Which existing harnesses can do this, and which need changes

| harness | chat roles it measures | takes `--model` | cached, paired comparison | timeout as failure |
| --- | --- | --- | --- | --- |
| `query_sufficiency` | sufficiency (quote arm = production) | yes | no | no (1800 s client timeout; failures skipped) |
| `query_citation` | answer + citation, production `answer()` | yes | no | no |
| `query_attribution` | citation compliance, es/en, short/long, small/large | yes | no | no |
| `query_entailment` | a judge over stored answers | yes | no | no |
| `query_limit` | answer recall at `limit` k (the k is the variable) | yes | no | no |
| `query_title` | the `--save` title (not one of the three roles) | yes | no | no |
| `query_grounding` | none: embedding model only | embed model | n/a | n/a |

Every chat harness already takes `--model`, and every one has a model-free
`--self-test`. None of them caches a generation, none counts a timeout as a
failure (they skip it, which lets a model raise its mean by timing out on hard
items), and none compares two models on paired items. Each also rebuilds the
index and re-embeds per run, so screening six models would re-run `bge-m3`
six times and swap it against every chat model.

So rather than retrofit six runners, this screen adds one runner that **reuses
their corpora and the production functions** and adds the three missing
mechanics once:

- **`ask_screen_core.py`** (standard library only, unit-tested in
  `tests/unit/test_ask_screen_core.py`):
  - the generation cache, keyed by sha256 of model tag + digest + seed +
    sampling options + the exact messages, stored outside the repo under
    `~/Dev/Projects/openkos-eval-runs/ask-screen/`;
  - the timeout rule (a late reply is a timeout; a timeout is cached and
    reused only under a budget no larger than the one it ran out of; an
    ordinary error is not cached);
  - the paired item-level bootstrap (10,000 resamples, seed 42; unpaired item
    sets are refused);
  - the successive-halving cut and the selection rule.
- **`run_ask_model_screen.py`**, in three stages:
  1. `contexts`: `bge-m3` only. It builds each bundle, runs the production
     `answer()` with a capturing stub in place of the chat model, and stores
     each item's exact user message. Every model then sees byte-identical
     prompts, and the embedding model never co-resides with a chat model.
  2. `generate`: one chat model. It calls the production
     `_context_holds_the_answer` and the synthesis prompt through the cache,
     with seed = run number, and parses with the production
     `_split_attribution`.
  3. `compare`: model-free. It reads the cache's projection (`runs/`), and
     reports the paired deltas, vetoes, halving cut, selection and the
     cross-lingual penalty.

The existing harnesses are unchanged. They remain the single-model probes for
the questions they were written for. `query_entailment`'s judge
(`gpt-oss:20b`, judge-only) is an optional secondary on finalists, not part of
the selection.

## Items

Two sets of the same 32 Spanish questions, so that the cross-lingual penalty
is itself a paired, per-question difference:

| set | bundle | items |
| --- | --- | --- |
| `es-es` | `grounding_corpus.DOCS` (Spanish, 14 docs) | 20 transferred + 12 anchored |
| `es-en` | `attribution_corpus.EN_DOCS` (its English mirror) | the same 32 |
| `es-built` | `--built-bundle`: compiled by `openkos ingest` with the #1557/#1558 extraction choice | the same 32 (pending) |

The cross-lingual questions and how their gold is derived without annotation
are in `evals/query_grounding/crosslingual_corpus.py`:

- **The 20 existing questions transfer.** Their `grounded` / `adjacent` labels
  carry over, because the English bundle states the same facts document by
  document.
- **Twelve new anchored questions** each have a language-invariant answer
  token (a person's name or a meeting number) and one verbatim evidence phrase
  per bundle language. The gold documents are computed as the documents whose
  body contains an evidence phrase, and the gold answer is "the answer states
  the token". `check_items` verifies mechanically that each phrase is in the
  bundle, the derived gold set is non-empty, each token sits inside its
  evidence and no token appears in its own question. This is the
  `query_limit` construction (gold = documents carrying a unique code the
  answer must state), moved to tokens a translation cannot change.

## Metrics per role

Per item and run; a failed call (timeout or error) scores **0**, never "skipped".

| role | item score | items |
| --- | --- | --- |
| sufficiency | 1 when the verdict is right (grounded: SUFFICIENT; adjacent: `NONE`) | all |
| answer | share of the gold tokens the answer states (diacritics- and case-insensitive; numbers by value) | anchored only |
| citation | grounded: a reported, non-empty `USED:` line naming a derived gold document when one reached the context; adjacent: `USED: none` | all |

The guardrails and vetoes are in the pre-registration draft.

## Limits

- The bundles are 14 short synthetic documents. A number here ranks models
  on this shape of question; it is not an absolute quality figure.
- The answer metric checks a fact, not prose. Anchored tokens are names and
  meeting numbers only, and a numeric token matches any equal number in the
  answer.
- The `es-built` set inherits whatever the extraction dropped: a transferred
  "grounded" question may be unanswerable there. This is the same for every
  model, so the comparison is still paired, but its absolute rates mean less.
- Ollama may keep generating after a client timeout, so the next call would
  queue behind it. Stage 0 checks this before any timed round.
