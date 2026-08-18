# Does self-attribution make the citation list mean anything? (#753)

Needs Ollama for the embedding model AND the chat model — unlike its sibling
`query_grounding`, this probe must see what the model WRITES, not just what
retrieval ranks.

```
uv run python -u evals/query_citation/run_query_citation_probe.py --self-test
uv run python -u evals/query_citation/run_query_citation_probe.py --baseline
uv run python -u evals/query_citation/run_query_citation_probe.py --runs 3
uv run python -u evals/query_citation/run_query_citation_probe.py --rescore <runs.json>
```

## The baseline costs nothing

`--baseline` reads `evals/query_title/results/runs-*.json`, which another
experiment already paid for and which already record `cited` per answer.
Across its **170 stored answers** from the pre-#753 code path:

- distinct citation **counts**: **1** — every answer cited exactly `limit`;
- distinct citation **sets**: **4**, each "the whole corpus minus one".

One distinct count is the whole defect in a number. `citations` was assembled
before `llm.chat` ran and never compared to the reply, so it was
`min(limit, corpus)` renamed. `query --save` then wrote all of them as
permanent provenance, so it outlived the screen.

Note the corpus difference: those runs are on `query_title`'s six documents,
not on this probe's fourteen. The borrowed claim is "the list never varied
with the answer", which is a property of the old code path rather than of any
corpus — not a claim about this corpus's numbers.

## Result, 2026-08-17

`qwen3:8b`, 14 documents, 10 grounded / 10 adjacent questions, 3 runs,
60 answers.

| class | n | reported | absent | unparsed | compliance | kept share (mean) | kept share (median) | cited nothing |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `grounded` | 30 | 30 | 0 | 0 | 100% | 0.487 | 0.400 | 0 |
| `adjacent` | 30 | 30 | 0 | 0 | 100% | 0.140 | 0.000 | 22 |

Separation (grounded − adjacent, mean kept share): **+0.347**.

**POSITIVE.** All three failure modes the probe was written to catch came
back clean:

1. **Compliance** — 60 of 60 answers emitted a parseable line. Nothing fell
   back to `absent` or `unparsed`.
2. **Cosmetic** — the model does not simply name every block. Grounded
   answers keep about half their context; the pre-#753 value is 1.000 for
   everything, by construction.
3. **Discrimination** — adjacent answers, the ones the bundle cannot answer,
   keep 0.140 and **22 of 30 cite nothing at all**. That is #753's specimen
   arriving with zero citations instead of five.

The safety side is the column that matters most for `--save`: **0 of 30
grounded answers cited nothing**, so a question the bundle can answer never
loses its provenance and never has its filing refused.

## What this does NOT measure

Whether the blocks the model NAMES are the ones it actually drew on. That is
an entailment judgement and nothing here computes one. The labelled classes
proxy it only at the resolution of "should this answer lean on the bundle at
all" — so a clean split is evidence the mechanism discriminates, NOT evidence
that each individual attribution is accurate.

It also cannot tell a correctly-dropped grounded citation from a wrongly
dropped one. Grounded answers keeping 0.487 could be the model shedding three
irrelevant retrievals, or shedding two relevant ones; this probe cannot
separate those, and the number should not be read as either.

## Limits

One chat model, one embedding model, one synthetic (though #753-shaped)
corpus, 20 questions, 3 runs. Compliance in particular is a per-model
property: a weaker backend may not emit the line at all, which is exactly why
the fallback keeps today's behavior instead of emptying the citation list.
