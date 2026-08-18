# Can any signal tell that two filed insights are the same object? (#762)

Embedding calls only — zero chat calls. Every answer it scores was already
generated and stored by `evals/query_title/`.

The raw `pairs-*.json` is **not committed**: it holds one row per scored
pair, which is quadratic in the stored population — 14,365 pairs is 8 MB on a
single line. The report beside it carries every conclusion, and `--rescore`
reads a file you regenerate locally.

```
uv run python -u evals/query_identity/run_query_identity_probe.py --self-test
uv run python -u evals/query_identity/run_query_identity_probe.py
uv run python -u evals/query_identity/run_query_identity_probe.py --rescore <pairs.json>
```

## The question

#762's harm is an IDENTITY defect, not a titling one. #757 measured four
titling arms and every one scored exactly the 8-of-16 baseline: changing how
a title is derived changes which STRING the slug is, and does not make two
different strings the same object. #762 asks what signal decides "same", and
notes that #760 found embedding distance reports topical relatedness rather
than sameness.

That conclusion was measured on question-to-DOCUMENT distance. Here both
sides are filed insights, so it is re-measured rather than inherited.

## Result, 2026-08-18

`bge-m3`, 14,365 pairs over the 170 stored filings.

| signal | paraphrase worst | different best | margin | separates |
| --- | ---: | ---: | ---: | :---: |
| `title` (`resolution.similarity`) | 0.8421 | 1.0000 | **-0.1579** | no |
| `answer` body embedding | 0.8835 | 0.9455 | **-0.0620** | no |
| `question` embedding | 0.9719 | 0.8974 | **+0.0745** | **yes** |

**The shipped signal fails.** `near_match_score` is what identity already
runs on elsewhere in this codebase, and its best different-subject pair
scores a perfect **1.0000** — two questions about unrelated subjects
producing the identical title. A threshold on it merges strangers.

**The answer body fails too**, which is #760's conclusion holding in a new
regime: two answers about one topic are textually similar whether or not
they answer the same question.

**The source question separates**, and in hindsight that is what a
paraphrase IS. It is reachable at write time because `query --save` already
stores the question as the filed insight's `description`.

## The measurement that nearly went the other way

An earlier revision dropped every pair whose members lacked a subject key,
reasoning that absence of a family label is not proof of difference. That
left only cross-family negatives — `trazabilidad` against `fuentes
inmutables`, which share no vocabulary — and **every** signal separated,
`title` apparently perfectly, at margin +0.8421 on 484 pairs.

The dropped pairs were the hard negatives: same topic, different question
(`¿qué es la trazabilidad?` against `¿por qué es importante la
trazabilidad?`). `query_title`'s own probe table says those "ask different
things and SHOULD file as different objects". Scoring them took the negative
set from 484 to 13,084 and reversed two of the three verdicts.

## What shipped, and what deliberately did not

The margin is **+0.0745** and rests on two subject families. That is
evidence for showing a person, not for acting automatically — so
`query --save` DISCLOSES candidates in the preview a human already confirms,
and nothing is merged, renamed or refused. A false positive costs one
advisory line; a false negative costs exactly what happens today.

## Limits

Two subject families. The pair counts are large because each family recurs
across runs and generations, but they rest on two paraphrase relations, so
this measures the signal's behavior on that regime rather than estimating
how often users paraphrase. One embedding model, one corpus, one language.
