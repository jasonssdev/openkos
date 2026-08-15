# `participant_anchor` — is the participant anchor lexicon too tight? (#706)

Issue #690 ended with a diagnosis rather than a fix: the judge dropped the
`Person` candidates the #668 participant pass produced, and the anchor gate
then declined to re-admit them because `_has_participant_anchor` returned
false. PR #705 made that discard observable
(`ExtractionReport.participant_unreadmitted_discarded_titles`) and stopped
there, because the field carries TITLES and the gate reads `description` +
`body`. This probe records the text the gate actually judged.

> **The gate this probe measured is RETIRED (#712).** What it found is why:
> the gate read the candidate's own description — text the model wrote out of
> the capture prompt's vocabulary — so it was checking the prompt against
> itself. `_has_participant_anchor` and `_PARTICIPANT_ANCHOR_RE` survive with
> zero production callers so `--rescore` can still re-derive every number in
> `report.md` from `results/*.jsonl`, which is what that report promises in
> its opening lines. `--rescore` is unaffected: it scores the two lexicons
> against stored text and never consults production.
>
> A LIVE run now measures the pipeline WITHOUT the gate. The
> `unreadmitted-discarded` bucket — named `anchorless-discarded` before the
> retirement — is reachable only on a non-meeting-shaped source. No stored
> run carries the old label, because #706 measured zero discards across all
> nine runs, and that zero is the finding that filed #712.

```bash
uv run python -u evals/participant_anchor/run_participant_anchor_probe.py --self-test
uv run python -u evals/participant_anchor/run_participant_anchor_probe.py --runs 3
uv run python -u evals/participant_anchor/run_participant_anchor_probe.py --rescore
```

**Use `-u`.** Piping through `tee` makes Python buffer and a long run looks
hung.

## What it records, and how

A wrapper around `concept._select_with_progress` — the one seam that sees
the complete `list[ExtractionResult]` on its way to the judge — captures
every `Person`/`Organization` candidate with its description, its body, its
reported bucket (`judge-selected` / `re-admitted` / `unreadmitted-discarded`)
and the shipped lexicon's verdict on it. Production is untouched; the
wrapper delegates.

`--self-test` runs the whole path against a scripted backend with no model.
Its first assertion is that the seam captured anything at all: a renamed
`_select_with_progress` would patch nothing, every run would report zero
candidates, and the probe would still exit 0.

## The arms

| arm | role | what it is |
| --- | --- | --- |
| `es-anchored` | positive | Spanish coordination meeting, every person anchored in vocabulary the lexicon does NOT contain (`estudiante del magíster`, `tesista`, `dicto el ramo`, `dirijo el área`) |
| `es-bare` | control | Spanish operations meeting; people are named and nothing more, so every `Person` candidate is a bare-name stub BY CONSTRUCTION |
| `en-ami` | control | `TS3005a.transcript.txt`, real English corpus (present only after `decision_extraction/scripts/build_sources.py` has run) |

`es-anchored` is built from the shape #690 observed in the field — a
Spanish university transcript whose attendees were students, and whose
participant information survived only as institutional email addresses in a
query answer. It is an **existence test**, never a population estimate:
three constructed arms cannot say how often real transcripts take either
shape.

## The bar a widening must clear

The lexicon's own docstring records the asymmetry: a false negative keeps a
genuine participant dropped (the status quo), a false positive re-admits a
name-only stub — the flooding defect the gate exists to prevent. So the bar
is `evals/language_leak`'s #630 bar: the candidate widening is re-scored
across EVERY stored run and **one false positive rejects it**.

`--rescore` reads `results/*.jsonl`, so the verdict is reproducible with no
GPU and every later run tightens it rather than replacing it.

Which candidates are genuinely anchored and which are stubs is **not**
inferred — deriving it from any regex would beg the question. It lives in
`adjudication.json`, hand-written, keyed by `arm::type::title` so it does
not go stale when the model rewords a description on the next run. Anything
unadjudicated is reported as unadjudicated and counted in neither column.

## What it measured

See `report.md`.
