# section_coverage — can a per-section coverage signal see a lost section? (#793)

**Verdict, one predicate per line. `quote`, built on verbatim quoting, was
measured, REFUTED, and not shipped. `overlap`, built on content-word
overlap, has now been swept over a threshold ladder and it SEPARATES — the
reported failure high, healthy runs low — but only inside `B` ∈ [0.20, 0.25],
which is NOT the value its constant still holds, on 17 runs of one model,
with the window selected from two of the three arms it is reported from and
no under-fire arm on a discursive source. That is a MEASURED WINDOW, NOT A
VALIDATED DEFAULT. Nothing is shipped, and no constant here changed.**

#793 reports that `helios-overview.md` lost its whole `## Storage` and
`## Components` sections while `ingest` printed unqualified success, and
proposes the remedy itself:

> A per-source coverage signal (headed sections or declarative sentences that
> contributed no object) would make this visible without changing extraction.

This harness built that signal and measured it before wiring it into the
pipeline. It should not be wired in as built. What it now also carries is the
seam the refutation said a next attempt would need, a second predicate behind
it, and a ladder over that predicate's one swept constant.

```
uv run python -u evals/section_coverage/run_section_coverage_probe.py --self-test
uv run python -u evals/section_coverage/run_section_coverage_probe.py --runs 5
uv run python -u evals/section_coverage/run_section_coverage_probe.py --runs 3 \
    --source ~/corpus/transcript.md --source-title "Some Meeting"
uv run python -u evals/section_coverage/run_section_coverage_probe.py \
    --rescore evals/section_coverage/results/runs-20260821T233809Z-qwen3-8b.json \
    --predicate all
```

`--self-test` and `--rescore` make no model calls and need no Ollama.

Every published table below names the ONE command that regenerates it, and
every one of those commands is a `--rescore` of the committed sweep: no model
call, no GPU, no editing of a constant. `--overlap-threshold B` scores
`overlap` at `B` and can be repeated, so a whole ladder is one invocation and
each rung gets its own `overlap@B` column rather than eight columns all
labelled `overlap`. `--ablate` is the under-fire arm. Neither touches
`OVERLAP_COVERED_FRACTION`, which is still 0.5.

## The signal

`section_coverage.uncovered_sections(texts, source_text, predicate)` splits
the source at its ATX headings and reports the headings no object's written
text covers. What "covers" MEANS is the predicate's job, and that is the one
knob this harness exists to turn.

## The refutation criterion, fixed before any new number exists

Written here first, and printed at the top of every report the probe emits,
because a criterion recorded after the measurement is a criterion fitted to
it. A candidate predicate SHIPS only if:

- the reported #793 failure — `helios-overview` with `## Storage` and
  `## Components` lost — scores **HIGH** uncovered, and
- healthy runs on **ordinary** sources score **LOW**, above all on discursive
  meeting transcripts, which are the corpus openkos is for.

A candidate that cannot put the first above the second is **refuted**, in the
same words `quote` was. Not "needs tuning": `quote`'s two distributions did
not overlap, they inverted, and a signal whose distributions cross has no
threshold to tune to.

The probe computes both halves over one sweep and prints them side by side,
including what the reported failure would score under each predicate, so the
comparison can be read without arithmetic. The comparison IS the finding. One
column alone cannot tell an improvement from a relabelling.

## Predicate 1: `quote` — measured, refuted

The covering test is shipped `extraction/evidence.py`'s `evidence_line`,
unchanged, with the SECTION as the source — so "quoted" would have meant one
thing across both #801's object-side signal and this source-side one.

That reuse is also what killed it.

### The measurement

`qwen3:8b`, generation ceiling 8192, `union_judge` on — the shipped path.
Both committed fixtures are the exact bytes from the 0.2.8 E2E workspace.

| source | uncovered share of checkable text | sections flagged |
| --- | --- | --- |
| `helios-overview`, 5 ok runs | 0.0% every run | 0 of 4 |
| `kickoff`, 4 ok runs | 0.0% every run | 0 of 4 |
| the failure #793 reports | 62.0% | 2 of 4 |
| a real 9-heading transcript, 3 runs | **98.0%, 31.3%, 97.6%** | **7, 6, 7 of 8** |

The first two rows are `results/runs-20260821T233809Z-qwen3-8b.json`,
reproducible with `--rescore`. The third is a reconstruction from the
adjudicated outcome recorded in the fixture — this repo does not hold the
0.2.8 run's object texts, only the fact that those two sections produced
nothing — and the probe recomputes it per predicate
(`reported_failure_share`). An earlier sweep of the same two fixtures agreed
exactly — 0.0% on every successful run — and is not committed, since one
stored artifact per claim is enough.

The over-fire half — the half that could condemn the design — came back
clean on the committed fixtures: **0 false positives across 36
section-observations** (9 successful runs × 4 sections). Then the transcript
arm inverted it.

An ordinary meeting transcript scores HIGHER than the defect the signal was
built to catch. No threshold separates them; the distributions do not merely
overlap, they cross.

### Why: the predicate, not the aggregation

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

### Both aggregations were tried

- **Counting sections** floods. The real E2E corpus is 5 of 5 sources with
  headings, and its three transcripts carry **44, 41 and 9**. A 44-section
  source with a 24-candidate pre-judge ceiling *cannot* cover every section,
  so a count-based notice fires on every meeting.
- **Weighting by text** is the table above. It is the quantity #793 actually
  names — *"half the document was not represented"* — and it inverts.

So a next attempt has to change the covering PREDICATE.

## The seam: a predicate is a PAIR, never a bare function

`coverage_report` used `evidence_line` for **two** jobs at once:

- the COVERING test — *does any object quote a line of this section?*
- the CHECKABILITY gate — `is_quotable(section.body)`, which skips a section
  no object could ever quote, so the signal cannot produce a finding nothing
  can clear.

They are paired. Swap only the covering test and the gate goes on asking
"does this section contain a quotable line" — a question about a *different*
predicate. `checkable_chars` then measures one thing while `uncovered`
measures another, the share stops being a share of what was tested, and the
new column is silently not comparable to the committed ones **while looking
like it is**.

So `CoveragePredicate` is a named pair of `(covers, checkable)`, and the
invariant is documented on the type: *a section this predicate cannot score
meaningfully is skipped, and enters NEITHER total.* Each predicate expresses
that invariant its own way, and the two gates genuinely disagree — the
self-test pins both directions, because a single direction would also pass if
one gate merely delegated to the other:

| section | `quote` gate | `overlap` gate |
| --- | --- | --- |
| `de la que en el` — five function words | checks it (clears the 4-**word** evidence floor) | skips it (0 **content** words) |
| `Marta Ruiz` / `Tom Becker` on two lines | skips it (no line clears the floor) | checks it (4 content words) |

The report prints `skipped`, never `0%`, for a section a gate rejected. The
two mean opposite things, and the whole side-by-side table turns on the
difference.

## Predicate 2: `overlap` — measured. It SEPARATES, at B ∈ [0.20, 0.25]

A section is covered when the fraction of its **distinct content words**
appearing in the union of the objects' texts clears a threshold `B`. Content
words are casefolded and accent-folded (`Información` → `informacion`, the
corpus being Spanish and English), stopworded against one union list for both
languages, with digits kept (`MySQL 8`, `PostgreSQL 16`, `2026-03-15` are the
facts #793 complains about losing) and punctuation dropped. Its checkability
gate skips a section carrying too few distinct content words for a fraction
to be a measurement.

`OVERLAP_COVERED_FRACTION` was swept; `OVERLAP_MIN_CONTENT_WORDS` was held at
4 for every point on the ladder, so nothing below tests the gate. Same
conditions as `quote`: `qwen3:8b`, shipped path, generation ceiling 8192,
`union_judge` on.

### Arm 1 — under-fire, the reported failure, reconstructed by ablation

The five stored healthy `helios-overview` runs are rescored with all but
three objects removed — `Concept: Helios Data Platform`, `Person: Marta
Ruiz`, `Person: Tom Becker`, the exact three the 0.2.8 run produced — so the
surviving objects are the model's own real texts rather than section bodies
handed back to themselves. `## Storage` and `## Components` then have nothing
behind them, which is the reported defect.

Regenerate this table, no model calls:

```
uv run python -u evals/section_coverage/run_section_coverage_probe.py \
    --rescore evals/section_coverage/results/runs-20260821T233809Z-qwen3-8b.json \
    --ablate --predicate quote \
    --overlap-threshold 0.05 --overlap-threshold 0.10 \
    --overlap-threshold 0.15 --overlap-threshold 0.20 \
    --overlap-threshold 0.25 --overlap-threshold 0.30 \
    --overlap-threshold 0.40 --overlap-threshold 0.50
```

| B | ablated uncovered share, per run | names BOTH lost sections | full healthy run |
| --- | --- | --- | --- |
| 0.05 | 0.0%, 0.0%, 0.0%, 10.8%, 0.0% | 0/5 | 0.0% ×5 |
| 0.10 | 0.0%, 0.0%, 0.0%, 10.8%, 0.0% | 0/5 | 0.0% ×5 |
| 0.15 | 33.7%, 33.7%, 33.7%, 44.5%, 33.7% | **0/5** | 0.0% ×5 |
| 0.20 | 62.0%, 62.0%, 62.0%, 72.8%, 62.0% | **5/5** | 0.0% ×5 |
| 0.25 | 62.0%, 62.0%, 62.0%, 72.8%, 62.0% | 5/5 | 0.0% ×5 |
| 0.30–0.50 | 62.0%, 62.0%, 62.0%, 72.8%, 62.0% | 5/5 | 0.0% ×5 |

In that output the 0.50 rung is headed `overlap`, not `overlap@0.5`: 0.5 is
`OVERLAP_COVERED_FRACTION`, so it is the registry predicate itself and keeps
the name every committed number here was recorded under. Every other rung
carries its own threshold in its column header.

That command also prints a `quote` row, because the same ablation runs under
any predicate: `quote` scores **62.0%, 62.0%, 62.0%, 72.8%, 62.0%** and names
both lost sections in **5/5**, identical to `overlap` inside the window. The
two predicates lose the same two sections on this source, which is the
consistency check spelled out below and not a second finding.

Read the third column first. At 0.15 the share is already a third of the
source, and **not one run names both lost sections** — the signal is loud and
pointing somewhere else. The flip to 5/5 at 0.20 is the whole result, and it
is a flip in *what is named*, not in how much fires.

Two details that are not noise:

- **62.0% is exactly 276/445**, the same numerator over the same denominator
  as the figure this README publishes for `quote`. Two predicates with
  independent gates admit the same four sections on this source and lose the
  same two, so the same loss reproduces under a completely different covering
  test. That is a consistency check on the reconstruction, not a second
  finding.
- **The 10.8%/44.5%/72.8% column is one identifiable run, not variance.**
  Four of the five runs produce the same seven objects; run 4 produced four —
  `Helios Data Platform`, `Storage in Helios…`, `Components of Helios…`,
  `Ownership of Helios…`, the heading-restating run recorded under *Two
  collateral findings* below. It never produced the two `Person` objects, so
  the ablation leaves it one object rather than three and `## Ownership` goes
  uncovered too. Its column is higher because it is a worse run, which is the
  signal behaving.

### Arm 2 — over-fire, the discursive transcript that killed `quote`

The private 9-heading transcript, 8 runs. Its text and its objects are not
recorded anywhere in this repo; these shares are.

This is the one table here that cannot be regenerated from the repo, because
its source is not in the repo. Against your own copy of a transcript, the
command has the same shape and the same zero-model-call ladder:

```
uv run python -u evals/section_coverage/run_section_coverage_probe.py \
    --rescore <your sweep>.json \
    --overlap-threshold 0.05 --overlap-threshold 0.10 \
    --overlap-threshold 0.15 --overlap-threshold 0.20 \
    --overlap-threshold 0.25 --overlap-threshold 0.30 \
    --overlap-threshold 0.40 --overlap-threshold 0.50
```

`--source` runs are never written to `results/`, so producing that sweep in
the first place means a `--source` run whose report stays on the terminal.
`results/README-transcript-arm.md` is the per-section record kept from ours.

| B | uncovered share per run |
| --- | --- |
| 0.05 | 3% ×8 |
| 0.10 | 5%, 5%, 3%, 3%, 5%, 5%, 3%, 3% |
| 0.15 | 5%, 5%, 3%, 3%, 5%, 5%, 3%, 5% |
| 0.20 | 5%, 5%, 3%, 3%, 24%, 5%, 3%, 24% |
| 0.25 | 5%, 24%, 3%, 3%, 24%, 5%, 3%, 24% |
| 0.30 | 24%, 92%, 5%, 5%, 92%, 5%, 24%, 92% |
| 0.40–0.50 | 92%, 92%, 92%, 24%, 92%, 92%, 92%, 92% |

Those runs produced 8, 9, 9, 9, 5, 8, 9 and 6 objects. By 0.30 the arm is
back in `quote`'s failure mode — 92% uncovered on runs that extracted nine
objects — so the upper edge of the window is not a preference, it is where
the predicate stops working.

### Arm 3 — out-of-sample over-fire control

`kickoff`'s 4 stored healthy runs: **0.0% uncovered at B = 0.15, 0.20, 0.25
and 0.30**, on all four runs, with `NO OVER-FIRE` at every rung. This arm was
not part of choosing the window, and it is the only number here that was not.

```
uv run python -u evals/section_coverage/run_section_coverage_probe.py \
    --rescore evals/section_coverage/results/runs-20260821T233809Z-qwen3-8b.json \
    --overlap-threshold 0.15 --overlap-threshold 0.20 \
    --overlap-threshold 0.25 --overlap-threshold 0.30
```

The same invocation prints `helios-overview` above it, where all five stored
runs score 0.0% at every rung and the verdict is `BLIND` — collateral finding
1 below, that #793's reported defect no longer reproduces, restated under
`overlap`.

### The window, and what `quote` scored on the same 8 runs

**B ∈ [0.20, 0.25].** Below it the reconstructed failure is not named at all
(0/5 at 0.15); above it the transcript explodes (92% by 0.30). Inside it the
reported failure scores 62.0–72.8% while eight healthy transcript runs
score 3–5% and six of the eight stay at 5% or below.

For contrast, on those same 8 transcript runs `quote` scored **100.0%, 30.4%,
28.0%, 30.4%, 98.0%, 100.0%, 30.4%, 97.6%** against the defect's 62.0% —
still inverted, on four times the runs. Runs 7 and 8 produced the **same
object count (8)** and scored **30.4% and 97.6%**. Same source, same run
count, a 67-point spread: direct evidence that `quote` measures verbatim-
quoting luck rather than coverage.

### Four caveats, as load-bearing as the result

1. **The window was SELECTED by sweeping the same arms it is reported from.**
   Arms 1 and 2 chose [0.20, 0.25] and are then quoted as evidence for it.
   Only arm 3 is genuinely out of sample, and only for over-firing. This is a
   **measured window, not a validated default**, and it is why
   `OVERLAP_COVERED_FRACTION` is still 0.5: moving it is a shipping decision,
   and no arm here has earned one.
2. **The under-fire arm is ONE source, and it is terse and bullet-shaped** —
   the same shape `quote` also worked on. There is no under-fire arm on a
   discursive source, because no adjudicated per-section ground truth exists
   for one: nobody has said which sections of that transcript *should* have
   produced an object. That is the single biggest gap and the obvious next
   step.
3. **n = 5 + 8 + 4 runs, one model.** No second model, no second under-fire
   source, no repetition of the ladder.
4. **The transcript arm is UNADJUDICATED, and so was `quote`'s.** A run that
   genuinely extracted badly SHOULD flag many sections. Nothing in this arm
   separates "the signal over-fired" from "that run was bad", for either
   predicate. It does not overturn `quote`'s refutation — that rests on the
   hand-checked `## Resumen` paraphrase below, which is a mechanism, not a
   share — but it does mean the published 98.0% conflated two things and
   should be read as loudness, exactly as this directory labels it.

### What `overlap` does NOT fix

The hand-checked case that killed `quote` is still flagged. The README's own
`## Resumen` example scores **0.1818** — four of eleven content words — which
is below the measured window's own floor of 0.20. At a separating threshold
`overlap` still calls that correct extraction uncovered; it is outvoted by
the rest of the source rather than corrected. The probe's self-test pins that
number and says so.

### What would have to be measured before this could ship

- An **under-fire arm on a discursive source**: an adjudicated transcript
  where somebody has recorded which sections genuinely produced nothing.
  Without it, the only evidence that `overlap` catches a real loss comes from
  one terse bullet-shaped file.
- The window **re-measured on arms that did not choose it**, including a
  second model.
- `OVERLAP_MIN_CONTENT_WORDS` swept at all, rather than held at 4. The
  covering threshold now sweeps from the CLI; the gate does not, so every
  number here is still a number about this gate's 4 and none of them tests
  it. A next attempt wanting that ladder needs a second factory beside
  `overlap_predicate`, on the same terms: the swept value has to reach the
  predicate's `name`, or the column lies about itself.
- A decision on the 44- and 41-heading transcripts, where the pre-judge
  ceiling makes full coverage arithmetically impossible and the question is
  usefulness, not correctness.

Until then the constant stays at 0.5, `overlap` stays behind the seam, and
nothing is wired into `ingest`.

## The two options deliberately NOT tried

The refutation named three fuzzy predicates. One is implemented; the other
two are deferred, with reasons rather than as an oversight:

- **Embedding similarity.** Every section-by-object comparison becomes an
  embedding call. Measuring it contends with whatever sweep is on the GPU —
  Ollama serializes by default, so a probe run is not free of the experiment
  running beside it — and it costs real wall-clock time per calibration pass,
  where `overlap` and `quote` rescore a stored sweep in milliseconds.
- **Asking the model.** This puts a non-deterministic call on the common
  ingest path, for a signal whose entire appeal was costing nothing and being
  reproducible. It would also make the notice unreproducible from stored
  runs: `--rescore` is free precisely because every predicate so far is a
  pure function of text this repo already holds.

Neither is ruled out. Both need their own calibration, and neither should be
added without reading this section first.

## Rescoring is free, and that is the point

Every stored run carries its full `objects` (type/title/description/body), so
any predicate can be scored against a stored sweep with **zero model calls**:

```
--rescore <runs.json> --predicate quote --predicate overlap
```

`--rescore` therefore always RECOMPUTES from the stored objects rather than
reading the stored verdicts back, and `--predicate all` scores every
registered predicate in one invocation. `--overlap-threshold B`, repeatable,
adds one `overlap@B` column per rung, and `--ablate` reduces each stored run
to `Fixture.reported_objects` before scoring — so both published tables above
are one command each over a file this repo already holds. Neither touches
`OVERLAP_COVERED_FRACTION`: the threshold reaches the covering test through
`overlap_predicate(B)`, which names the predicate after the value it used, so
a swept column cannot be read as the default's.

The stored verdicts are `quote`'s, and the self-test pins that recomputing
them reproduces
`results/runs-20260821T233809Z-qwen3-8b.json` exactly — that equivalence is
the entire safety net under the refactor, since nothing else in the repo
would notice if the seam had altered the baseline.

A sweep costs minutes of GPU. A predicate scored against it costs nothing. No
future candidate should be rejected because re-measuring it was expensive.

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

`overlap`'s arm 1 does not break that rule, because it does not grade a run
against what it should have found. It ABLATES: a healthy run's objects are
cut down to the three the 0.2.8 run actually produced, so the loss is
constructed rather than judged, and what is scored afterwards is a known
outcome. That is still one reconstruction of one reported failure, and it is
counted as such.

## The private-corpus arm

`--source` measures a file from disk. The transcripts behind the fourth row
above, and behind `overlap`'s arm 2, carry real names and addresses and are **not** committed, on the same
footing as the gitignored AMI corpus `evals/decision_extraction/` reads. That
arm reports `UNADJUDICATED`: nobody has said which of its sections *should*
have produced an object, so it measures how LOUD the signal is there, not
whether it is right.

`--source` runs are never written to `results/`, under any predicate: their
extracted objects carry that private text onward, and a tracked directory is
not where it goes. `results/README-transcript-arm.md` records that arm's
per-section verdicts ONLY — never its text and never its objects — and
anything added there must keep to the same discipline.
