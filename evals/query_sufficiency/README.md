# Can a pre-synthesis sufficiency check refuse what attribution misses? (#760)

Needs Ollama for the embedding model AND the chat model.

```
uv run python -u evals/query_sufficiency/run_query_sufficiency_probe.py --self-test
uv run python -u evals/query_sufficiency/run_query_sufficiency_probe.py --runs 10
uv run python -u evals/query_sufficiency/run_query_sufficiency_probe.py --rescore <runs.json>
```

## The bar is not "does it separate the classes"

#760 proposed a sufficiency check after measuring that no relevance floor
exists on any retrieval signal. Between the issue being filed and this probe
being written, #753's citation half shipped (PR #763): the model now closes
its answer with a `USED:` line, and scored over the same corpus that already
refuses **7 of 10** adjacent questions with **0 of 10** false refusals, for
free.

So measuring "does a sufficiency check separate grounded from adjacent"
would come back POSITIVE and mean nothing. The bar is narrower:

**does a pre-synthesis call catch the 3 adjacent questions attribution
misses, while refusing none of the grounded 10?**

The three survivors, with their per-run citation counts under attribution:

```
[0, 2, 5]  ¿cuáles son las mejores prácticas de chunking...?
[3, 3, 2]  ¿cómo se evalúa la calidad de un sistema de recuperación...?
[2, 2, 2]  ¿qué relación hay entre la trazabilidad y la verdad...?   <-- #753's own
```

The last is #753's reported failure verbatim, and #760 measured it as NEARER
to the corpus than three grounded questions. An arm that misses it has not
solved the defect that opened the issue.

## Result, 2026-08-18

`qwen3:8b`, 14 documents, 10 grounded / 10 adjacent questions, 10 runs,
400 checks.

| arm | grounded refused (any run) | adjacent refused (all runs) | survivors caught | median s |
| --- | ---: | ---: | ---: | ---: |
| `binary` | **1 of 10** | 10 of 10 | 3 of 3 | 0.85 |
| `quote` | **0 of 10** | 10 of 10 | **3 of 3** | 1.12 |

- **`binary`** ("reply SUFFICIENT or INSUFFICIENT") — **NEGATIVE**. It
  false-refused `¿quiénes participaron en las reuniones?`, a question #753's
  own table lists as working. That is the exact cost that rejected the ruled
  distance floor, reproduced by a different mechanism.
- **`quote`** ("quote the sentence that answers, or NONE") — **POSITIVE**.
  Zero false refusals across 100 grounded checks, all three survivors
  caught, at a median 1.12s per non-refused query.

**Two arms, not one, is what made this readable.** A single `binary` arm
would have come back negative and licensed the conclusion "a sufficiency
check does not work" — when what does not work is asking for a verdict.
Making the model produce the evidence first and deriving the verdict from
whether it found any is the whole difference. This repo has rejected four
prompt-level treatments on measurement; this is the first time the
FORMULATION rather than the mechanism was the variable.

## Cost

The check runs before synthesis, so it saves the synthesis call on a refusal
and is pure added latency otherwise. A default Ollama serialises, and
attribution already handles 7 of 10 adjacent questions, so most queries pay
the median 1.12s and get nothing they did not already have. That is why the
shipped `sufficiency_check` is a config key rather than a constant.

## What this does not measure

Answer QUALITY after a SUFFICIENT verdict. The probe never calls synthesis,
so it says whether the gate would have opened, not whether what came through
was any good.

One chat model, one embedding model, one synthetic (though #753-shaped)
corpus, 20 questions. Compliance and calibration are per-model properties; a
different backend needs its own run.
