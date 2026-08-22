# section_coverage — can a per-section coverage signal see a lost section? (#793)

**Verdict: NO, not one built on verbatim quoting. Measured, refuted, not shipped.**

#793 reports that `helios-overview.md` lost its whole `## Storage` and
`## Components` sections while `ingest` printed unqualified success, and
proposes the remedy itself:

> A per-source coverage signal (headed sections or declarative sentences that
> contributed no object) would make this visible without changing extraction.

This harness built that signal and measured it before wiring it into the
pipeline. It should not be wired in.

```
uv run python -u evals/section_coverage/run_section_coverage_probe.py --self-test
uv run python -u evals/section_coverage/run_section_coverage_probe.py --runs 5
uv run python -u evals/section_coverage/run_section_coverage_probe.py --runs 3 \
    --source ~/corpus/transcript.md --source-title "Some Meeting"
uv run python -u evals/section_coverage/run_section_coverage_probe.py --rescore <runs.json>
```

`--self-test` makes no model calls and needs no Ollama.

## The signal

`section_coverage.uncovered_sections(texts, source_text)` splits the source
at its ATX headings and reports the headings no object's written text quotes
a line of. The covering test is shipped `extraction/evidence.py`'s
`evidence_line`, unchanged, with the SECTION as the source — so "quoted"
would have meant one thing across both #801's object-side signal and this
source-side one.

That reuse is also what killed it.

## The measurement

`qwen3:8b`, generation ceiling 8192, `union_judge` on — the shipped path.
Both committed fixtures are the exact bytes from the 0.2.8 E2E workspace.

| source | uncovered share of checkable text | sections flagged |
| --- | --- | --- |
| `helios-overview`, 5 ok runs | 0.0% every run | 0 of 4 |
| `kickoff`, 4 ok runs | 0.0% every run | 0 of 4 |
| the failure #793 reports | 62.0% | 2 of 4 |
| a real 9-heading transcript, 3 runs | **98.0%, 31.3%, 97.6%** | **7, 6, 7 of 8** |

The first three rows are `results/runs-20260821T233809Z-qwen3-8b.json`,
reproducible with `--rescore`. An earlier sweep of the same two fixtures
agreed exactly — 0.0% on every successful run — and is not committed, since
one stored artifact per claim is enough.

The over-fire half — the half that could condemn the design — came back
clean on the committed fixtures: **0 false positives across 36
section-observations** (9 successful runs × 4 sections). Then the transcript
arm inverted it.

An ordinary meeting transcript scores HIGHER than the defect the signal was
built to catch. No threshold separates them; the distributions do not merely
overlap, they cross.

## Why: the predicate, not the aggregation

`evidence_line` tests VERBATIM quoting. Extraction over discursive text
paraphrases. Hand-checked on that transcript's `## Resumen`:

> source: *"El equipo definió el alcance del sistema y acordó usar minutas
> reales para validar la arquitectura propuesta."*
>
> object produced: `Decision: Uso de Minutas Reales para Validación`
>
> `evidence_line(...)` → `None`

The object plainly covers the section. The section is flagged. That is a
false positive on a *correct* extraction, on a marker #793 would have made
non-retryable.

The signal works on terse, declarative, bullet-shaped sources, where
extraction does quote — `helios-overview` and `kickoff` are exactly that
shape, which is why the first two rows look so good. It fails on meeting
transcripts, which is the corpus openkos is for, and nothing tells the two
apart in advance.

## Both aggregations were tried

- **Counting sections** floods. The real E2E corpus is 5 of 5 sources with
  headings, and its three transcripts carry **44, 41 and 9**. A 44-section
  source with a 24-candidate pre-judge ceiling *cannot* cover every section,
  so a count-based notice fires on every meeting.
- **Weighting by text** is the table above. It is the quantity #793 actually
  names — *"half the document was not represented"* — and it inverts.

A next attempt has to change the covering PREDICATE: token overlap,
embedding similarity, or asking the model. Each is a different signal with
its own calibration, and the fixtures and probe here are set up to measure
one.

## Two collateral findings

1. **#793's reported defect no longer reproduces.** All 5 runs of
   `helios-overview` cover `## Storage` and `## Components`, producing
   `Concept: MySQL 8`, `Ingest workers`, `Query API` and `Redis cache`. The
   0.2.8 run produced 3 objects; today's produce 5 to 9.
2. **3 of 10 `kickoff` runs died with `OllamaGenerationCapped`** at 8192
   tokens on a **631-byte** source, across two sweeps, taking 222 and 238
   seconds before failing. A runaway rate that high on a source that small
   is worth its own look, and a runaway generation is exactly what would
   make the judge call in #795 time out.

3. **Covering a section does not mean covering it well.** One run answered
   `helios-overview` with exactly four objects — `Helios Data Platform`,
   `Storage in Helios Data Platform`, `Components of Helios Data Platform`,
   `Ownership of Helios Data Platform` — one per heading, each restating its
   own section title. The signal flagged nothing, correctly: those objects
   do quote their sections. Coverage is a floor, not a quality measure, and
   the restatement failure that #585 and #801 catch is a different question
   from this one.

## What is automated, and what is not

The **over-fire** half is mechanical: every section in a fixture's
`must_stay_quiet` produced objects in the reported run, so a flag there is a
false positive. The whole `kickoff` fixture is that check at source scale.

The **under-fire** half is not, deliberately. Whether a given run lost a
section is a fact about that run's objects, and extraction is stochastic —
#793 says so itself. A run that lost nothing SHOULD flag nothing, and
scoring that as a miss would punish the signal for the model behaving. The
probe prints every run's objects beside its flags and leaves that reading to
a person rather than inventing a second heuristic to grade the first.

Two verdicts it does compute, because no reading rescues either: `VACUOUS`
(every checkable section flagged in every run) and `BLIND` (nothing flagged
at all on the source the issue was filed about).

## The private-corpus arm

`--source` measures a file from disk. The transcripts behind the fourth row
carry real names and addresses and are **not** committed, on the same
footing as the gitignored AMI corpus `evals/decision_extraction/` reads.
That arm reports `UNADJUDICATED`: nobody has said which of its sections
*should* have produced an object, so it measures how LOUD the signal is
there, not whether it is right.
