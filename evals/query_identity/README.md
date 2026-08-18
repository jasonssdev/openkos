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

## Result, 2026-08-18 (later) — the +0.0745 margin DOES NOT SURVIVE depth

The result above rested on **two** paraphrase relations, and this README said
so under Limits. `_PROBES` now carries **eleven** families, and the shipped
question signal was re-scored over them with
`--questions` (embeddings only, zero chat calls, one pair per unique QUESTION
pair rather than per stored filing — the earlier 14,365 was inflated by each
question recurring once per run and generation).

| population | paraphrase pairs | worst | different pairs | best | margin |
| --- | ---: | ---: | ---: | ---: | ---: |
| 11 families | 35 | 0.4380 | 526 | 0.9152 | **-0.4772** |
| contested calls dropped | 25 | 0.8343 | 353 | 0.9152 | **-0.0809** |

**The question signal does not separate.** The +0.0745 measured over two
relations was a property of a thin corpus, not of the signal. No threshold
splits paraphrases from same-topic-different-question pairs here, which is the
same shape the title and answer signals failed in — this signal just needed
more relations to show it.

**The ground truth was authored by the party measuring against it**, so the
verdict is reported both ways and the contested calls were flagged in
`_PROBES` before any score was seen. The two worst paraphrase pairs (0.4380,
0.4853) are both `MVP` against `producto mínimo viable` — contested call 2,
and an ACRONYM problem rather than a paraphrase one; #397 already resolves
acronym identity downstream, so the embedding is the wrong instrument for it.
Dropping every contested call still leaves the margin negative, so the verdict
is not an artifact of the calls.

### What this does and does not condemn

It does NOT make `DUPLICATE_QUESTION_SIMILARITY = 0.93` unsafe. At that value
the signal discloses **0 of 526 strangers** — the best negative is 0.9152,
below the threshold — while catching 11 of 35 paraphrases (11 of 25 with
contested calls dropped). It errs toward silence, which is what an advisory
that costs one preview line should do.

What it does condemn is the JUSTIFICATION. The module docstring of
`src/openkos/resolution/insight_identity.py` presents `+0.0745 | yes` as the
reason the question signal was chosen, and that row is now known to hold only
at n=2 relations. The mechanism ships on "zero false positives and some
recall", not on "this signal separates the classes". Those are different
claims and only the first is supported.

## Limits

**Superseded in part** — the "two subject families" limit below was the
original one, and acting on it is what produced the depth result above. Read
them in that order: the table near the top of this file is the n=2 result the
depth run refutes, kept because the code it justified still ships.

Eleven families now, over one domain, one embedding model and one language.
The families still do not estimate how OFTEN users paraphrase — that is a
usage rate, and no fixture produces it. What they now support is the narrower
and more useful claim: whether a threshold on this signal can separate the
classes at all. It cannot.

Nothing here measures recall under `DUPLICATE_SCAN_LIMIT` (#764) either. That
is a question about where duplicates sit in FILING ORDER, not about the
signal, and it needs usage traces rather than more questions.
