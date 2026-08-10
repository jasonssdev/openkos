# The collapse is predicted by whether the source announces its own topics

Canonical run: `qwen3:8b`, 10 runs per arm, 3 pairs plus the positive
control. 2026-08-10, issue [#522][522].

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

## Next

The constraint in #522 stands: a prompt change must be A/B'd through
`evals/extraction_cap/` and judged on whether it fixes the collapse. This
probe is what makes the second half of that possible. The candidate it points
at is the prompt's singular framing — "A document explaining one topic
usually yields exactly ONE object", every type bullet reading "the candidate
is ONE specific, named X" — since the model appears to need the *source* to
supply a multiplicity signal the *prompt* never does.

[522]: https://github.com/jasonssdev/openkos/issues/522
