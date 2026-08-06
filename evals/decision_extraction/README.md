# `decision_extraction` — can extraction reach the nine OKF types?

Measures the real extraction pipeline over [AMI Meeting Corpus](https://groups.inf.ed.ac.uk/ami/corpus/)
scenario meetings, using AMI's own human annotations as independent evidence
about what each source contains. Manual tool, not pytest, not shipped.

```bash
# 1. build sources + ground truth from the AMI annotations
uv run python -u evals/decision_extraction/scripts/build_sources.py \
    --zip ~/Downloads/ami_public_manual_1.6.2.zip

# 2. measure which types extraction actually emits
uv run python -u evals/decision_extraction/scripts/run_type_coverage.py --runs 3
uv run python -u evals/decision_extraction/scripts/run_type_coverage.py --self-test  # no model needed
```

**Use `-u`.** Same reason as the other harnesses: piping a run through `tee`
makes Python buffer, and a long run then looks hung.

## The confound this exists to break

Every end-to-end run measured before this one used edited prose about software,
and the bundles came out ~79% `Concept`: only `Concept`, `Event`, `Person` and
`Procedure` ever appeared across 19 objects. `Decision`, `Entity`,
`Organization`, `Place` and `Project` never did.

That observation has two explanations that predict it identically — *the
classifier cannot reach those types*, and *the corpus contains none of them* —
and re-running that corpus can never separate them. Course material about
protocols genuinely has no meetings, no companies, no named decisions.

AMI breaks the tie because it ships a human-annotated named-entity layer
(`PERSON`, `LOCATION`, `ORGANIZATION`) written years before this project
existed. That is evidence about the SOURCE, not about the extractor.

## The one inference the annotations license

Deliberately one-directional, and the harness's value depends on not widening
it:

| Mentions | Objects | Verdict |
|---|---|---|
| 0 | 0 | **explained** — the corpus afforded nothing |
| many | 0 | **UNEXPLAINED ABSENCE** — the corpus explanation is ruled out |

What it does **not** license is the reverse. A meeting naming the Project
Manager seventeen times does not mean extraction should emit seventeen `Person`
objects, or even one — a mention is a span of words, an object is a subject
worth a document. Scoring emitted objects *against* mention counts would reward
over-production, and [`run_cap_eval.py`](../extraction_cap/run_cap_eval.py)
already argues why a scorer that can be generous toward the hypothesis measures
nothing.

So mentions are an **affordance floor**, never a target.

## What it cannot answer, stated rather than fudged

- **`Decision`** has no named-entity backing. Its affordance comes from AMI's
  abstractive `decisions` section — a summary-level claim rather than a span
  annotation, weaker evidence, and labelled as such in the report.
- **`Project`** and **`Entity`** have neither, so their counts are reported as
  observations and explicitly excluded from the verdict rather than given a
  made-up proxy.

## Two variants per meeting

Each meeting yields a verbatim `*.transcript.txt` **and** AMI's own
`*.summary.txt`. Not for coverage — to isolate a second confound. AMI
transcripts are spoken language with disfluencies and overlapping turns, and
every corpus measured before was edited prose. Running only the transcript
could not distinguish *"the classifier does not reach these types"* from *"the
extractor does not digest speech"*. The summary is the same content in the
register the extractor has been tested on.

## Reproducibility

`ami_public_manual_1.6.2.sha256` pins the exact archive and
`manifests/ami_selected_28.txt` pins the exact meetings, so anyone who
downloads AMI gets the same bytes and therefore the same numbers.

That is the difference from [`examples/extraction-corpus/`](../../examples/extraction-corpus/),
whose own `.gitignore` states the cost plainly: *"nobody else can reproduce the
exact numbers, only the procedure."* That corpus is third-party course material
this Apache-2.0 repository cannot relicense. **AMI is CC BY 4.0**, so the
constraint here is different — generated sources stay out because they are
derived and regenerable, not because they cannot be shared.

## Layout

| Path | |
|---|---|
| `scripts/build_sources.py` | AMI XML → sources + ground truth. Never runs a model. |
| `scripts/run_type_coverage.py` | Runs extraction, tabulates types, issues the verdict. |
| `manifests/ami_selected_28.txt` | 7 scenario series × 4 sessions (`a`–`d`). |
| `ground_truth/` | AMI's own `decisions` sections, verbatim. |
| `report.md` | The canonical latest run. |
| `sources/`, `results/`, `.ami-unpacked/` | Generated; gitignored. |

Splitting build from measure is deliberate: a bad transcript build must not be
mistakable for a bad extraction result. `build_sources.py` refuses to write a
transcript whose segments resolved to nothing, because an earlier version of it
silently emitted a plausible-looking 132-character file for a thirty-minute
meeting after a namespace typo.
