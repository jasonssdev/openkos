# The collapse is predicted by whether the source announces its own topics

Canonical run: `qwen3:8b`, 10 runs per arm, 3 pairs plus the positive
control. 2026-08-10, issue [#522][522].

The `lesson` pair and the negative control were added **after** this run and
appear nowhere in it — see
[Added after this run](#added-after-this-run-the-lesson-axis-and-the-negative-control).

```
uv run python -u evals/extraction_collapse/run_collapse_probe.py --runs 10
```

## Verdicts

| pair | axis | treatment | floor | verdict |
| --- | --- | --- | --- | --- |
| `producto` (ES) | meeting register | meeting 9/10 collapsed | flat 10/10 collapsed | `NO FLOOR` |
| `versioning` (EN) | meeting register | meeting 0/10 collapsed | flat 5/10 collapsed | `NOT REPRODUCED` |
| `anuncio` (ES) | announced topics | unannounced **10/10** collapsed | announced **2/10** collapsed | `AXIS IMPLICATED` |

Positive control `TS3005b.summary.txt`: collapsed **10 of 10**, always to a
single object titled `Remote Product Development`. The probe can see the
defect, so the verdicts above are about the fixtures.

## #522's own hypothesis did not survive its pairs

Neither meeting-register pair implicated the register. `producto` collapsed on
**both** arms, so it affords no floor and says nothing. `versioning` collapsed
on neither. A source recording a meeting is not, by itself, what produces the
single object.

## What did survive, on content it was not generated from

The announced-multiplicity idea came out of `producto` by accident:
shortening the meeting arm's opening from

> Nos reunimos hoy con el equipo de producto **para revisar el onboarding y
> un par de temas que quedaban pendientes.**

to

> Nos reunimos hoy con el equipo de producto.

was a change made only to satisfy `MAX_LENGTH_SKEW`, and it took that arm
from 2 collapses in 10 runs to 10 in 10. Re-running that same text would
re-fit the hypothesis to the observation that produced it, so `anuncio` was
written from scratch — a support queue, a different decision, new subjects —
with both arms in the meeting register and only the announcement moving.

It replicated: **10/10 against 2/10.**

## The aggregate, across three sources and two languages

Counting every arm by whether its opening sentence enumerates the source's
topics:

| opening | arms | collapsed |
| --- | --- | ---: |
| enumerates topics | `versioning` meeting (×2 runs), `anuncio` announced | 2 / 30 |
| does not | `producto` meeting (×2 runs), `anuncio` unannounced, control | 54 / 55 |

**The control fits the pattern, and that is the load-bearing part.**
`TS3005b.summary.txt` is an abstractive summary with no sentence enumerating
its topics, and it collapses 25 of 25 across every run this probe has made.
The announcement hypothesis explains the very case #522 was built on. The
meeting-register hypothesis does not: both `producto` and `anuncio` treatment
arms *are* meetings, and only the ones that fail to announce collapse.

## What the hypothesis does NOT explain

`versioning`'s flat arm announces its topics — "The API versioning work and a
couple of loose ends on the platform stand as follows" — and still collapses
5 of 10 (7 of 10 in an earlier run). Announcement alone does not cover that,
so register may interact rather than being irrelevant. This is the best
available explanation, not a complete one.

The effect size is worth stating plainly too: the announced arm holds a mean
of **1.8** objects, not 3. The contrast is one object against two.

## The path production actually runs

Everything above is `extract_concept`, the single-pass extractor. But
`DEFAULT_UNION_JUDGE` is `True`, so the shipped configuration runs
`extract_concept_union` — two passes unioned, plus a selector judge. The
probe was measuring a path most users never take.

Re-measured with `--union-judge`, same model, 10 runs per arm:

| pair | axis | treatment | floor | verdict |
| --- | --- | --- | --- | --- |
| `producto` (ES) | meeting register | meeting 10/10 collapsed | flat 9/10 collapsed | `NO FLOOR` |
| `versioning` (EN) | meeting register | meeting 0/10, mean **4.5** | flat 0/10, mean **4.5** | `NOT REPRODUCED` |
| `anuncio` (ES) | announced topics | unannounced **10/10** | announced **0/10** | `AXIS IMPLICATED` |

Control: still 10/10.

**The collapse survives union+judge**, which is what #522 already suspected
and this confirms on the default path. The announcement axis comes out
*cleaner* here than single-pass — 10/10 against 0/10, where single-pass gave
10/10 against 2/10 — and `versioning`'s two arms return **identical** means
with zero collapses either side, so the meeting register does nothing at all.

## `[]` does not reproduce on the default path (#524)

The empty returns recorded below are single-pass only: **0 empties in 70
union-path runs**. The second pass covers the first, so [#524][524] is real
but confined to `union_judge: false`, which is not the default. The rate
measured single-pass is an upper bound on the shipped configuration, not its
rate.

That also argues against fixing it with prompt wording: a change aimed at a
5% defect on a non-default path risks the default path, which is how the
`Decision` rubric change and the anti-twin clause (`concept.py:145-148`) both
went wrong before.

## Two observations recorded for follow-up, not interpreted here

**The survivor is the source-title twin.** Every ES collapse returned one
object titled exactly what the source is titled — `Reunión con el equipo de
producto`, `Reunión con el equipo de soporte`. That is precisely the twin
`_drop_source_title_twins` exists to remove, and it survives because it is
the only candidate: dropping it would leave zero objects. The deterministic
guard cannot reach the case that matters most.

**Runs are returning `[]`.** `producto` meeting run 8 and `versioning`
meeting run 3 both produced zero objects, as did one run in an earlier
session. The prompt carries an explicit positive default — a source with
substantive content yields AT LEAST ONE object, and `[]` is a last resort for
blank or unintelligible input. Three violations are not noise. This deserves
its own issue.

## Added after this run: the `lesson` axis and the negative control

Two fixtures were added after the canonical run above, so **no number in it
describes either**.

| fixture | axis | why it exists |
| --- | --- | --- |
| `lesson` (EN) | short lesson framing: an umbrella-topic title plus an opening sentence naming the lesson | the reported shape — a titled lesson whose body covers three distinct sub-subjects — has no fixture in this repository |
| negative control (unpaired) | none; it is not a pair | a genuinely single-subject source where **one object is correct**, so a change that returns `[]` there is visible |

Both are written to 1–4 KB, the size a course lesson file is. Every other
fixture reachable from `evals/` is either 600–800 B of meeting or flat
statements, multi-subject expository prose at 7.6–17 KB
(`extraction_cap/`), or an AMI transcript. So the false-positive rate for
any change to `_drop_source_title_twins` has, until now, been measurable only
against multi-subject prose — which is the gap
`measure_single_object_rate.py` records under `SINGLE_SUBJECT_UNMEASURED`.

The negative control does **not** go through `verdict()`, and must not: that
function maps `retained == 1` to a collapse by construction, so running a
source whose right answer is one object through it would report the correct
result as the defect. It runs unpaired, like the positive control, and gets
its own section.

## Their first run measured the instrument, and both halves failed

`qwen3:8b`, `--runs 5 --seed 7`, single-pass. Both fixtures have since been
rewritten, so these numbers describe **the earlier text, not what ships**.
They are recorded because each one found a defect in the instrument.

**The negative control could not fail.** It held at one object in 5 of 5 and
the probe printed *no false positive … the twin shape the floor keeps*. Every
lone object came back as a `Procedure`, and `_is_twin` exempts `Procedure` by
type (#413) — so it would have survived with both of the rule's floors
deleted. The control was green on a case with no red available, and
`title_twin_runs` had compared titles without ever consulting the type.

Fixed on both sides. The fixture is now a definition (`Replica Lag`) rather
than a scheduled job, so the prompt's own tie-break — *a page explaining what
a tool is is a Concept; a page of steps is a Procedure* — does not route it to
the exempt type. And the count is now type-aware: `title_twin_runs` counts
only droppable twins, `exempt_twin_runs` counts the exempt ones, and a run
whose lone object is exempt reads `NO FLOOR EVIDENCE` — *the control could
not have failed here, which is not the same as passing*. The fixture makes
the right outcome likely; only the harness change makes the wrong one visible.

**The `lesson` pair afforded no floor.** Both arms collapsed 5 of 5 —
`Procedure` on the lesson arm, `Concept` on the flat one — and the probe
correctly reported `NO FLOOR`. Three subjects were present but written as one
connected setup narrative, and the extractor read the narrative. Each
paragraph now leads with its own named artifact (`.venv`, the lockfile, the
tests tree) and carries its own consequence; the arms went from 1.1 KB to
about 1.7 KB to pay for it, well inside the band's 4 KB ceiling.

Context, not excuse: the same run left the three original pairs at 2
`INVERTED` and 1 `NOT REPRODUCED`, so this model and size may simply not
reach the collapse today. A floor arm still has to afford a floor. If it
cannot inside 1–4 KB, the honest outcome is `NO FLOOR` plus a note that the
size does not reach the question — **not** a fourth subject and not a longer
document. The band is the fixture's point and is the last thing to spend.

Same limitation as everything else here: constructed, not adjudicated.

## Their second run: both halves now work, and the first `AXIS IMPLICATED` pair

`qwen3:8b`, `--runs 5 --seed 7`, single-pass — same model, same seed, same
flags as the run above, so the two are directly comparable.

| fixture | result |
| --- | --- |
| `lesson` treatment | 1 object in 5 of 5, always `Procedure` |
| `lesson` floor (`untitled`) | **3 objects in 5 of 5**, `Concept` |
| verdict | **`AXIS IMPLICATED`** |
| negative control | 1 object in 5 of 5, `Concept`, **droppable twins 5/5, exempt 0/5** |

**The reported shape reproduces.** A short lesson under an umbrella-topic
title collapses to one object while the same three facts, retitled and
unframed, yield three. That is the first pair in this harness to implicate
its axis on constructed material, and it puts the observation behind #551 on
measured ground for the first time.

**The negative control now carries evidence.** Its lone object is a
`Concept` restating the source title in every run — a *droppable* twin. Its
survival is therefore attributable to the floor at `concept.py:474-475` and
to nothing else, which is exactly the reading the earlier `Procedure` version
could not support. Any change that narrows that floor and returns `[]` here
is now visible as a false positive.

### What this measures about the twin rule, and what it does not

The `lesson` treatment arm's lone object came back as a `Procedure` in every
run. `_is_twin` exempts `Procedure` by type (#413), so on this fixture the
twin rule would not have dropped that object under any floor — the mechanism
proposed as the fix cannot reach the shape it was proposed for.

That is one fixture at one model and one size, not a general claim. But it
points the same way as the rest of the evidence here: the collapse is a
*generation* outcome, and a filter downstream of generation can convert one
weak object into zero, never one into three. The floor arm proves the three
objects were available to be found.

## Next

The constraint in #522 stands: a prompt change must be A/B'd through
`evals/extraction_cap/` and judged on whether it fixes the collapse. This
probe is what makes the second half of that possible. The candidate it points
at is the prompt's singular framing — "A document explaining one topic
usually yields exactly ONE object", every type bullet reading "the candidate
is ONE specific, named X" — since the model appears to need the *source* to
supply a multiplicity signal the *prompt* never does.

[522]: https://github.com/jasonssdev/openkos/issues/522
