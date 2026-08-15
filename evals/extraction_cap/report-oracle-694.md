# The extraction oracle — #694

`qwen3:8b`, union+judge, 5 runs, 2026-08-15, on the new
`medium-10-reunion-plataforma` fixture.

**Reproduce it:**

```bash
uv run python -u evals/extraction_cap/run_cap_eval.py \
  --fixture medium-10-reunion-plataforma --runs 5 --union-judge on \
  --output evals/extraction_cap/results/<your-file>.md
```

The raw `results/` artifacts of this sweep are **not committed** — this
harness's `.gitignore` treats them as ephemeral, and the numbers below are the
committed record. That is workable here only because the *fixture and its
ground truth are tracked*, unlike the rest of the corpus: anyone cloning can
re-run the command above and get comparable numbers rather than having to take
this table on faith.

> `report.md` in this directory is still the 2026-08-06 full-corpus sweep and was
> deliberately left alone. A single-fixture run must not overwrite the
> corpus-wide record — pass `--output`, which this sweep did only after
> overwriting it once and restoring it.

## What #694 asked for, and what was already here

The issue reads as "build ground truth". Most of it already existed: four
annotated fixtures under `examples/extraction-corpus/ground-truth/`, an
exact-only matcher, and a per-position verdict vocabulary (`SUBJECT`, `FACET`,
`NEAR_DUPLICATE`, `OUT_OF_SCOPE`, `UNJUDGED`).

Three things were genuinely missing, and they are what shipped:

1. **Precision.** The harness measured recall only. A run could recover every
   subject and bury them in noise and score identically to a clean one.
2. **Variance.** Every figure was a single mean. #694's own evidence is that
   yield on a byte-identical source moved ~40% across three runs; a mean with no
   spread beside it invites exactly the misreading the issue is about.
3. **A transcript-shaped, non-English fixture.** Every corpus source was prose,
   and `small-04`'s ground truth already closed by saying extraction quality on
   non-English sources was unmeasured. Meeting-shaped Spanish is the regime
   where #713, #714 and #715 all lived.

## The baseline

| metric | value |
| --- | --- |
| subject recall | **0.89 ±0.04 [0.82–0.91]** n=5 |
| subject precision | **0.95 ±0.08 [0.83–1.00]** n=5 |
| mean produced | 10.40 |
| near-duplicates per run | 0.40 |
| known facets produced (decay) | 0.00 |
| unjudged titles | 0.00 |
| subjects lost to the cap | 0.00 |
| distinct title sets | **5/5** |
| backend errors | 0 |

## The headline finding: the variance is mostly in the NAMES

**Five runs produced five different title sets — and recall moved only from
0.82 to 0.91.**

That reframes #694's own evidence. The issue counted objects (12, 15, 9 across
three runs) and concluded that the count alone cannot say which run to trust.
Correct — but the reason is now visible: the runs largely recover *the same
subjects under different names*.

| the same subject, five ways |
| --- |
| `Latencia de búsqueda` · `Latencia de búsqueda vectorial` · `Problema de latencia en búsqueda vectorial` |
| `Modelo de embeddings` · `Cambio de modelo de embeddings` · `Cambio de versión en el modelo de embeddings` |
| `Incidente de caída del servicio` · `Incidente de la semana pasada` |

Each run emitted exactly one variant per subject, so this is naming variance,
not fragmentation. It is invisible to a count and invisible to a diff of two
bundles, which is why "extraction improved" was unfalsifiable.

A metric that matches on exact titles would have scored these as five different
answers. The `## Aliases` section is what converts them into one, and it is
adjudicated from real runs rather than guessed — a rule this corpus set for
itself and which binds hardest on a fixture whose source the annotator wrote.

## What the oracle catches that a count cannot

**A subject that is never recovered at all.** `Duplicación de objetos por
procesamiento en trozos` is missing from **5 of 5** runs. It is the one item
raised off the agenda, late in the source, in the last third. A count of 10
produced objects reads as healthy; the oracle says one designed subject never
survives.

**Fragmentation, distinguished from renaming.** Runs 2 and 5 emitted `Problema
de respaldos` *alongside* `Cifrado de respaldos` — two objects for one subject,
in one reply. That is #699's within-source fragmentation, on a fixture built to
exercise it: the backup arc spans a window boundary and each window named it
independently. It scores as `NEAR_DUPLICATE`, not as a second subject, so it
costs precision instead of inflating recall.

**Self-repetition.** Run 5 emitted `Rotación de credenciales` twice, verbatim.
Precision credits a subject once, so the repeat counts against the run — which
is why run 5 is the 0.83 floor of the precision range despite producing the
most objects.

## How precision is defined, and why

Share of the **judged** positions that earned a **new** subject.

- The denominator excludes `UNJUDGED`, and `mean unjudged titles` is printed
  directly beside it. Precision over a mostly-unjudged reply is not a
  measurement; a reader must be able to see how much of the output the figure
  covers. On this sweep it is 0.00, after the queue was worked.
- A run whose every title is unjudged has **no** precision and is excluded from
  the mean rather than scored zero. Scoring it zero would report an unannotated
  ground truth as a model failure.
- A subject already credited earlier in the same reply does not earn credit
  twice. `classify` answers per title, so two positions naming one subject both
  return `SUBJECT` unless the curated near-duplicate list happens to name the
  second. Counting both would let a run inflate precision by repeating itself.
  Mutation-verified: removing that rule moves the self-test's repeated-subject
  case from 0.50 to 1.00.

## Bounds, and the one that matters most

One model, one fixture, five runs. The other four corpus fixtures were not
re-swept, so their numbers in `report.md` remain the 2026-08-06 figures and do
not carry precision or variance yet.

**The ground truth is authored, not found.** The source was written for this
purpose, so its subject list is the author's design rather than a reading of
someone else's document. That removes the usual risk (a missed subject) and
introduces a different one: a claim measured against this oracle is measured
against a judgment made by the same hand that wrote the text. The 11 subjects
and 18 facets are recorded with the prose that supports each, precisely so the
owner can disagree with any of them — and **until they are adjudicated, any
"extraction improved" claim riding on this fixture inherits that circularity.**

The aliases and near-duplicates do not share that problem: every line came out
of a real adjudication queue, never a guess.
