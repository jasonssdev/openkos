# Is there a relevance floor? (#753)

Embedding calls only — zero chat calls. Builds a 14-document Spanish bundle
shaped after #753's evidence, indexes it through production's own `reindex`,
and reads the dense neighbourhood of 20 labelled questions.

```
uv run python -u evals/query_grounding/run_query_grounding_probe.py --self-test
uv run python -u evals/query_grounding/run_query_grounding_probe.py --runs 3
uv run python -u evals/query_grounding/run_query_grounding_probe.py --rescore <runs.json>
```

## Result, 2026-08-17 — the ruled remedy is not implementable on this signal

`bge-m3`, 14 documents, 10 grounded / 10 adjacent questions, 3 runs.

| reading | grounded worst | adjacent best | margin | separates |
| --- | --- | --- | --- | --- |
| `best` | 1.1762 | 1.0123 | **-0.1639** | no |
| `gap` | 0.0006 | 0.0870 | **-0.0864** | no |
| `mean_top3` | 1.2251 | 1.0754 | **-0.1496** | no |

Every reading OVERLAPS. There is no threshold that refuses the ungrounded
question #753 reports without also refusing questions the bundle answers —
and among the casualties are questions **the issue itself lists as working
correctly**:

- `¿quiénes participaron en las reuniones?` (best 1.0754)
- `¿qué aportó Gustavo Martínez al proyecto?` (best 1.0673)
- `¿qué decisiones se tomaron en las reuniones?` (best 1.0106, refused by a
  `gap` floor)

#753's own failing question — `¿qué relación hay entre la trazabilidad y la
verdad contextual en sistemas RAG?` — sits at **1.0123**, nearer than three
grounded questions, with its nearest neighbour being
`sources/reunion-01-trazabilidad`.

## Why that is not a surprise once you look at the pipeline

The issue proposes a floor and names the root cause correctly: "retrieval
takes the top-k regardless of score, so a weak best-match looks identical to
a strong one." The pipeline is worse than that phrasing suggests.

`fusion.fuse` is reciprocal rank fusion. A document's score is
`1/(60 + rank)`, summed over the channels that returned it. **That encodes
position and nothing else** — the top result scores identically whether it is
a perfect match or the least bad of ten irrelevant documents — and `fuse`
returns bare `concept_id`s, discarding even that. So the fused ranking cannot
carry a floor at all, and `FtsHit.score` is bm25, a magnitude that moves with
corpus statistics and query length rather than one comparable across
questions.

`VecHit.distance` is the only signal that is both a magnitude and comparable
question to question. It is the one measured here, in three readings, and it
does not separate the classes.

The reason is the defect's own premise: the question is *adjacent* to the
corpus, which means it shares the corpus's vocabulary. `trazabilidad`,
`citas`, `RAG` are all genuinely in `reunion-01`. A question about
traceability IS near a transcript about traceability. Embedding proximity
measures topical relatedness, not whether the documents ANSWER the question —
the same conclusion #402/#434 reached about seeded PageRank, in a different
part of the pipeline.

## What this does and does not license

It licenses: **do not ship a distance threshold.** Adopting one across an
overlap would be the first treatment in this repository adopted against its
own evidence, after four prompt-level treatments were rejected on measurement
(#613, #622, #618's first arm, #716).

It does not license: "grounding cannot be checked." It says this SIGNAL
cannot check it. Distinguishing "the context is about this topic" from "the
context answers this question" is a judgement about entailment, and nothing
in the retrieval pipeline computes one. A sufficiency check — a second, cheap
model call over the assembled context, before synthesis — is the obvious
candidate and is a different mechanism than the one ruled, so it needs its
own decision and its own measurement.

Worth separating, too: the answer being ungrounded and the CITATIONS being
unsupported are two defects. The second is fixable without any floor, by
citing only what the answer actually draws on, and does not depend on
resolving the first.

## Limits

One embedding model, one corpus, 20 questions. The corpus is synthetic though
shaped after the real one, and its documents are short. A larger bundle would
give grounded questions better matches and might widen the margin — but the
overlap here is large (0.16 on `best`, against a grounded spread of 0.39),
not marginal, and the offending adjacent question is the reported one.
