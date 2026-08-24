# section_coverage — can a per-section coverage signal see a lost section? (#793)

**Verdict, one predicate per line. ALL THREE ARE REFUTED, and the answer to
the question in the title is NO on the evidence assembled here.** `quote`,
built on verbatim quoting, was measured and refuted first. `overlap`, built
on content-word overlap, separated on `qwen3:8b` inside `B` ∈ [0.20, 0.25] —
and on 2026-08-23 the three gaps that verdict named were closed, and it
failed all three. A second model (`phi4:14b`) OVER-FIRES at every rung on
`kickoff`, the one arm that was genuinely out of sample and the floor the
window rested on. A leave-one-section-out arm — the under-fire arm on a
discursive source the refutation asked for — finds it BLIND to a majority of
constructed losses at that same window. Sweeping the second constant
(`OVERLAP_MIN_CONTENT_WORDS`) does not rescue it: raising the gate only
deletes the sections it was failing on. **`embedding`**, the semantic
candidate the refutation had left untried, was measured on 2026-08-24 under
the shipped `bge-m3` and OVERLAPS on both models — see *Predicate 3* below;
the reconstruction of the reported failure itself is what breaks it.
**Nothing is shipped, no constant changed, and `ingest` is untouched.**

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

```
uv run python -u evals/section_coverage/run_section_coverage_probe.py \
    --rescore evals/section_coverage/results/runs-20260821T233809Z-qwen3-8b.json \
    --leave-one-out --overlap-threshold 0.20 --overlap-min-words 4 \
    --overlap-min-words 8
```

`--self-test` and `--rescore` make no model calls and need no Ollama.

`--leave-one-out` is the under-fire arm that needs no adjudication and so
reaches a discursive source; `--overlap-min-words W` sweeps the second
constant and is crossed with every `--overlap-threshold`, since the gate
moves the denominator.

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

## Predicate 2: `overlap` — separated on one model, then REFUTED

**Read this section together with *The window does not survive a second
model* below.** What follows is the ladder as it was measured on `qwen3:8b`,
kept verbatim because it is the evidence the window was built from; the
refutation that followed is not a correction to it but a second measurement
it did not survive.

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

**This arm is the one that later collapsed.** On `phi4:14b` the same
`kickoff` fixture over-fires at every rung, including 0.15. Since it was the
only out-of-sample evidence the window had, its collapse is the refutation:
see *The window does not survive a second model*.

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

This list was written on 2026-08-22 and is kept verbatim, because what
happened next is that three of its four items were measured and the window
did not survive them. The sections after it carry the numbers.

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

## The window does not survive a second model — `overlap` is REFUTED

**2026-08-23.** The list above asked for the window to be re-measured on a
model that did not choose it. It was, on `phi4:14b` — a different family, not
a different size of the same one — with the same fixtures, the same 5 runs
each and the same ladder.

```
uv run python -u evals/section_coverage/run_section_coverage_probe.py \
    --runs 5 --model phi4:14b
uv run python -u evals/section_coverage/run_section_coverage_probe.py \
    --rescore evals/section_coverage/results/runs-20260823T150831Z-phi4-14b.json \
    --overlap-threshold 0.15 --overlap-threshold 0.20 \
    --overlap-threshold 0.25 --overlap-threshold 0.30
```

`kickoff` is the arm that matters, and it is the one this directory already
named as its only genuinely out-of-sample evidence. On `qwen3:8b` it scored
**0.0% uncovered at 0.15, 0.20, 0.25 and 0.30, on all four runs, with
`NO OVER-FIRE` at every rung.** That was the floor the whole window rested
on. On `phi4:14b`, same fixture, same rungs:

| rung | verdict | uncovered share per run |
| --- | --- | --- |
| `overlap@0.15` | **OVER-FIRES** | 42.9%, 42.9%, 0.0%, 17.6%, 25.3% |
| `overlap@0.2` | **OVER-FIRES** | 42.9%, 42.9%, 0.0%, 17.6%, 25.3% |
| `overlap@0.25` | **OVER-FIRES** | 42.9%, 42.9%, 17.6%, 17.6%, 36.7% |
| `overlap@0.3` | **OVER-FIRES** | 42.9%, 42.9%, 17.6%, 17.6%, 36.7% |

Per section, and this is the sharper reading: `## Context` produced objects
in the reported run and is flagged in **60% of runs at every rung including
0.15**, and `## Open questions` in 60–80%. `helios-overview` over-fires too,
on `# Helios Data Platform (HDP) — Overview` at 20–40%.

Nothing about the window was rescued by a lower rung. There is no value of
`B` at which this model's healthy runs stay quiet on `kickoff`, so the
window is not a window — it is a property of `qwen3:8b`'s output on two
files.

**The criterion at the top of this file was fixed before any predicate was
written, and `overlap` now fails it on the same terms `quote` did.** Healthy
runs on an ordinary source do not score LOW. Both predicates in this
directory are refuted, and the answer to the question in the title is, on the
evidence assembled here, NO.

## The under-fire arm on a discursive source: leave-one-section-out

The gap this directory named as its biggest: *"an adjudicated transcript
where somebody has recorded which sections genuinely produced nothing"*. It
was closed WITHOUT adjudication, because adjudication turned out not to be
what the arm needs.

`--ablate` reconstructs one reported failure by keeping the objects that one
real 0.2.8 run produced. That pins it to a fixture somebody itemised by
hand. `--leave-one-out` constructs the loss per SECTION instead: delete the
objects that verbatim-QUOTE a section, then ask the predicate again. The
outcome is known before the predicate is asked, so nothing is graded against
what it should have found — the probe's whole under-fire discipline is
preserved — and it works on any source with headings, including a private
transcript, because the output is counts.

```
uv run python -u evals/section_coverage/run_section_coverage_probe.py \
    --rescore evals/section_coverage/results/runs-20260821T233809Z-qwen3-8b.json \
    --leave-one-out --overlap-threshold 0.20 --overlap-threshold 0.25
```

### Attribution must not be the predicate under test

The first version of this arm scored `quote` at **100.0% over 36 trials**,
and that number measured nothing at all. `quote` covers by
`evidence.evidence_line`; the attribution deletes objects by
`evidence.evidence_line`; so the section is uncovered afterwards **by
construction** and every trial is a hit.

A predicate cannot be asked at runtime which rule it is — `covers` is an
opaque callable, and inferring the answer from behaviour would be guessing
about the one thing that must not be guessed. So `CoveragePredicate` now
carries `covers_by_quoting`, `leave_one_section_out` RAISES on it, and the
table prints `NOT SCORABLE` beside the name rather than dropping the row. A
third predicate built on `evidence_line` has to set that flag rather than
remember a caveat.

Two more rows are excluded for the same reason and none of them is a hit
declined: a section no object quotes has no constructed loss, and a section
every object quotes empties the list, where any predicate flags everything.

### What it found

`BLIND` is the column to read: a section that was covered, lost every object
quoting it, and was **still** called covered — a real loss this signal would
not have reported.

This table takes THREE commands, not one, and says so rather than leaving a
reader to discover it — the two committed sweeps rescore for free, and the
third row cannot be regenerated from anything this repo holds:

```
# rows 1-2, and rows 3-4, from the two committed sweeps -- no model call
uv run python -u evals/section_coverage/run_section_coverage_probe.py \
    --rescore evals/section_coverage/results/runs-20260821T233809Z-qwen3-8b.json \
    --leave-one-out --predicate overlap \
    --overlap-threshold 0.20 --overlap-threshold 0.25
uv run python -u evals/section_coverage/run_section_coverage_probe.py \
    --rescore evals/section_coverage/results/runs-20260823T150831Z-phi4-14b.json \
    --leave-one-out --predicate overlap \
    --overlap-threshold 0.20 --overlap-threshold 0.25

# row 5 -- a PRIVATE source, never stored to results/, so this one costs GPU
# and reproduces only for somebody holding that file
uv run python -u evals/section_coverage/run_section_coverage_probe.py \
    --source <transcript.md> --source-title "<title>" --runs 5 \
    --leave-one-out --overlap-threshold 0.20 --overlap-threshold 0.25
```

| source | model | rung | trials | NAMED | BLIND | hit rate | excluded |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `helios-overview` | qwen3:8b | `overlap@0.2` / `@0.25` | 20 | 11 | **9** | 55.0% | 0 |
| `kickoff` | qwen3:8b | `overlap@0.2` / `@0.25` | 16 | 12 | **4** | 75.0% | 0 |
| `helios-overview` | phi4:14b | `overlap@0.2` / `@0.25` | 7 | 5 | **2** | 71.4% | 13 unquoted |
| `kickoff` | phi4:14b | `overlap@0.2` / `@0.25` | 6 | 1 | **5** | 16.7% | 14 unquoted |
| `transcription2` (private), obs. 1 | qwen3:8b | `overlap@0.2` / `@0.25` | 5 | 0 | **5** | **0.0%** | not recorded |
| `transcription2` (private), obs. 2 | qwen3:8b | `overlap@0.2` / `@0.25` | 6 | 0 | **6** | **0.0%** | 34 unquoted, 10 unscorable |

The last two rows are the ones the refutation asked for, they are two
INDEPENDENT 5-run observations of the same source, and both are unanimous:
on a real discursive transcript, at the measured window, the signal caught
**none** of the constructed losses. In the same runs it flagged
`## Información de la reunión` and `## Notas de Gemini` on every run, both of
which produced objects. It is not merely quiet in the wrong place — on this
source it is anti-correlated with what it claims to measure.

The `excluded` column is the denominator, and it is why it exists. It is
also the column that caught a false zero in this table's first draft, where
the two `phi4:14b` rows were written as `0` without being measured.

Read down it and the rows stop being comparable, which is the point:

- **`qwen3:8b` on the committed fixtures excludes nothing.** Every section is
  checkable and quoted, so 20 and 16 trials are those sources entire.
- **`phi4:14b` on the SAME two fixtures excludes 13 and 14.** Same sources,
  same sections — a different model, whose objects quote the source verbatim
  far less often. Its `16.7%` therefore rests on 6 trials drawn from 20
  section-runs, not on the whole file.
- **The transcript excludes almost all of it** — 6 scorable trials out of 50
  section-runs, 34 dropped because nothing quoted the section.

Without this column `0 of 5`, `0 of 6` and `1 of 6` read as measurements of
comparable things. They are not, and the gap is not noise: how much of a
source this arm can reach at all is itself model-dependent.

### Three caveats, and the first one is large

1. **5 and 6 trials on the discursive source, out of 50 section-runs.** The
   arm can only score a section some object QUOTES, and discursive
   extraction rarely quotes verbatim — the same fact that refuted `quote`.
   A 0-of-6 is not a rate; it is six failures in a row where a working
   signal should have produced hits, twice over, and it is reported as such.
   The exclusion counts are printed beside every tally so this cap is
   visible rather than inferred.
2. **Trials are pooled across runs and sections**, so a run with more
   quoting objects contributes more rows. `ok runs` is printed beside every
   tally for that reason.
3. **`overlap` at its own default 0.5 scores better here** (100% on both
   qwen3 fixtures) — and 0.5 is the value the over-fire ladder rules out
   entirely. That is the whole shape of the result: the thresholds that see
   a loss are the thresholds that flag healthy sections, and no rung does
   both.

## The second constant, swept at last

`OVERLAP_MIN_CONTENT_WORDS` was held at 4 for every number published before
today. `overlap_predicate` now takes it, names the predicate `overlap@B/W`
when it is not the default, and the CLI crosses it with every threshold —
the gate moves the DENOMINATOR, so a share is not comparable across two
gates and pairing each threshold with one gate would hide exactly that.

```
uv run python -u evals/section_coverage/run_section_coverage_probe.py \
    --rescore evals/section_coverage/results/runs-20260821T233809Z-qwen3-8b.json \
    --leave-one-out --overlap-threshold 0.20 --overlap-threshold 0.25 \
    --overlap-threshold 0.30 --overlap-min-words 4 --overlap-min-words 8 \
    --overlap-min-words 12
```

Raising the gate does not improve the signal; it removes the sections the
signal was failing on. On `helios-overview` the leave-one-out trials fall
from 20 to 15 as W goes 4 → 8, and on `kickoff` from 16 to 8 at W = 12 —
where the hit rate then reads 100% over half the evidence. A gate is not a
tuning knob for this predicate. It is what decides which sections are
allowed to count, and every rung of it that looks better looks better by
discarding a harder case.

The threshold ladder is also **flat between 0.20 and 0.30** on these
fixtures under leave-one-out: identical trials, identical hits, at all three
rungs. The separation the earlier ladder measured lives in the over-fire
half only.

## Predicate 3: embedding similarity — measured (2026-08-24), and REFUTED

The refutation above named embedding similarity as one of two candidates
deliberately not tried. It is now tried:
`run_embedding_coverage_probe.py` scores every section of both committed
sweeps (`qwen3:8b` and `phi4:14b`, the same stored runs every prior verdict
used) by **max cosine similarity** under the shipped embedder (`bge-m3`,
the model `state/vectorstore` retrieves with). Attribution stays the
verbatim-quoting rule while scoring is by embedding, so the trials are not
decided by construction; the raw statistic is stored per row
(`results/embedding-coverage-20260824T154528Z.json`), so every threshold is
swept offline and `--rescore` re-renders with no embed call.

**It OVERLAPS on both models, and the reported failure itself is what
kills it.**

| model | lowest covered section | highest constructed/reported loss |
| --- | --- | --- |
| `qwen3:8b` | **0.3368** | 0.7375 |
| `phi4:14b` | **0.5626** | 0.7200 |

A threshold catching every loss flags sections that produced objects, on
both models — the zero-false-flag criterion fixed at the top of this file.
The most instructive row is the one this directory's own fixture predicted:
`## Ownership` produced two CORRECT `Person` objects in the reported run,
and against those objects it scores **0.3368** (`qwen3:8b`) — far BELOW the
genuinely lost `## Storage` at 0.5927–0.6039. A `Person` object's body is
written about the person, not about the section line that named them, so a
correct extraction does not resemble its source section — the same
inversion that refuted `quote` (discursive extraction rarely quotes) and
`overlap` (a healthy transcript out-scored the constructed loss), now
reproduced on the third and last cheap representation of "resembles".

The leave-one-section-out cells alone would have flattered it: `phi4:14b`
separates on both fixtures under LOSO (0.8827 vs 0.6180; 0.7576 vs 0.7200)
— and then the reconstruction of the ACTUAL 0.2.8 failure, the one case
the signal exists to catch, is what breaks the window on both models. A
verdict read off the constructed-loss cells alone would have shipped a
signal refuted by its own motivating case.

## The one option still NOT tried

- **Asking the model.** This puts a non-deterministic call on the common
  ingest path, for a signal whose entire appeal was costing nothing and being
  reproducible. It would also make the notice unreproducible from stored
  runs: `--rescore` is free precisely because every other predicate is a
  pure function of text and vectors this repo already holds or can
  regenerate.

It is not ruled out, but three failed representations of "the objects
resemble the section" (verbatim, lexical, semantic) now say the difficulty
is not the representation: a correct extraction legitimately does not
resemble its source section, and any similarity-shaped signal inherits
that.

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

## Three collateral findings

(The heading said "Two" while listing three items, from the round that added
the third. Counted, not renumbered.)

1. **#793's reported defect no longer reproduces.** All 5 runs of
   `helios-overview` cover `## Storage` and `## Components`, producing
   `Concept: MySQL 8`, `Ingest workers`, `Query API` and `Redis cache`. The
   0.2.8 run produced 3 objects; today's produce **4 or 7**.

   *Corrected 2026-08-23.* This line read "5 to 9" until somebody counted
   the committed sweep instead of quoting the sentence: the five stored
   `qwen3:8b` runs produce 7, 7, 7, 4 and 7 objects, so neither bound was
   right and no run ever produced 5, 6, 8 or 9. The 4 is run 4, which is the
   one-object-per-heading run collateral finding 3 describes. `phi4:14b`
   produces 4, 4, 4, 3 and 5. Regenerate with:
   `python3 -c` over `results/*.json`, or read the `objects per run` block
   any `--rescore` prints.
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

`--leave-one-out` extends that same licence rather than widening it. It
constructs the loss per SECTION by deleting the objects that quote one, so
the outcome is again known before the predicate is asked and again nothing
is graded against what it should have found. What it buys is reach: an
ablation keyed to one hand-itemised run works on two fixtures, while a
per-section deletion works on any source with headings — which is how the
under-fire half finally got measured on a discursive transcript. What it
costs is a new way to be vacuous, and `covers_by_quoting` is the guard: see
*Attribution must not be the predicate under test*.

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
