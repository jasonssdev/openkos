# Is the answer entailed by the context? (#774)

Needs Ollama for the embedding model AND the chat model.

```
uv run python -u evals/query_entailment/run_query_entailment_probe.py --self-test
uv run python -u evals/query_entailment/run_query_entailment_probe.py --runs 3      # pilot
uv run python -u evals/query_entailment/run_query_entailment_probe.py --arms <runs.json>
uv run python -u evals/query_entailment/run_query_entailment_probe.py --rescore <arms.json>
uv run python -u evals/query_entailment/run_query_entailment_probe.py \
    --workspace <path> --runs 15 --question "¿qué es X?"   # field mode (gitignored output)
```

**Measured verdict: `results/report-774-verdict.md`.** The constructed
corpus never fabricated (30 of 30 compliant — it measures the false-flag
cost side); the fabrication only reproduced in field mode against the real
degraded bundle, where `attribution == "absent"` caught 30 of 30 fabricated
answers with 1 of 45 grounded false-positives, both chat-judge formulations
died on false flags, and the `--save` gate shipped on that evidence.

## The empty row this measures

0.2.7 shipped two guards and #774 happened with both active and neither
firing: `sufficiency_check` (#760) verifies the context *could* answer,
citation attribution (#753) records what the model *says* it used, and
nothing verifies **the answer is entailed by the context**. The field
specimen was a fabricated NLP treatise carrying five bundle citations,
contradicting the concept it cited first.

## The fabrication class is constructed to reproduce that mechanism

`fabrication_corpus.py` adds five concepts that define familiar-sounding
technical terms idiosyncratically (the real specimen `orística` among them):
retrieval finds the definition, the sufficiency check correctly passes, and
a model that "recognizes" the term is invited to answer from pretraining —
which is the defect, verbatim. The corpus is CONSTRUCTED, like every fixture
in `evals/`: read results as mechanism-consistency, not field rates.

## Exposure before any verdict

The report prints **`answers it could fail: M`** — fabrication-class answers
actually produced. `M = 0` means UNFALSIFIABLE, never shippable (the #706
lesson). The pilot (`--runs 3`) exists to measure M cheaply before the
full-n verdict is paid for; the final bar needs adjudicated per-answer
labels and n=15.

## Two phases so the paid part is paid once

Generation stores each production answer WITH its context blocks; the three
arms (`unsupported` evidence-first, `binary` control, `lexical`
deterministic) score stored pairs and can be re-run without re-paying
generation.

## The cost side comes first

A grounded answer flagged is a FALSE FLAG and is reported before any
benefit: an arm that buys the fabrications by flagging grounded answers has
reproduced the refusal defect #753's distance floor was rejected for.
