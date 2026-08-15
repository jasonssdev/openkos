# What the participant-anchor probe measured (#706)

qwen3:8b, 3 arms × 3 runs, 2026-08-15. 9 runs attempted, 8 answered, 27
`Person`/`Organization` candidates recorded with the description and body
the gate actually reads.

Every number below is re-derivable from `results/*.jsonl` with `--rescore`;
no verdict here rests on a single run.

## The headline: the gate discarded nobody, and the lexicon is blind anyway

| arm | candidates | judge-selected | re-admitted by the gate | **anchorless-discarded** |
| --- | --- | --- | --- | --- |
| `es-anchored` (positive) | 14 | 14 | 0 | **0** |
| `es-bare` (control) | 9 | 0 | 9 | **0** |
| `en-ami` (control) | 4 | 4 | 0 | **0** |

**#706's hypothesis did not reproduce as a cost.** The anchor gate rejected
zero candidates across every arm and every run. Nothing was lost to it here,
so nothing measured here argues that the gate is what cost #690 its `Person`
objects on a different source.

**But the lexicon is blind, and the runs prove it twice over.**

## Blind in one direction: a stated role it cannot see

12 of the 27 candidates state a role explicitly and score ANCHORLESS:

| candidate | description the gate read | runs |
| --- | --- | --- |
| Germán Vega | `Representative from Vega Ingeniería` | 3/3 |
| Jason Sepúlveda | `PhD Student` / `Master's student in information retrieval laboratory` | 3/3 |
| Gustavo Martínez | `Responsible for data ingestion pipeline` | 1/3 |
| Vega Ingeniería | `Company providing digitized minutes corpus` / `Data Area` | 2/3 |
| B, C, D (AMI) | `User Interface Designer`, `Industrial Designer`, `Marketing Expert` | 1/1 |

`Representative from` is the sharpest of these: the lexicon carries
`representative of`. A **preposition** is the whole difference between a
recognised affiliation and a name-only stub. The AMI row matters more than
the constructed ones — that is a real corpus, and three of its four
participants state their function in the first minute of the meeting and
score anchorless.

Every one of these survived only because the judge happened to select them.
The gate is a **latent trapdoor**: harmless on these runs, and the exact
mechanism that fires on the run where the judge does not.

## Blind in the other direction: a stub it admits every time

`es-bare` is the control where the source states **nothing** about anyone —
three names, three sets of turns, no role, no affiliation, no relation.

All 9 candidates it produced were **re-admitted by the anchor gate**, on
every run. The token that admitted them, every time, was `Participante`:

> `Participante en la reunión, inició el bloque 1 y discutió sobre el
> estado del índice...`

The source never says that. The **model** wrote it, and the phrasing comes
from `_PARTICIPANT_CAPTURE_SYSTEM_PROMPT`, which tells the model that
"spoke in this meeting, attended" are acceptable anchors.

## What that means, stated plainly

`_has_participant_anchor` reads the **candidate's own description and
body** — the model's paraphrase — not the source. So:

- a candidate the model describes in the prompt's own vocabulary passes,
  whether or not the source supports a word of it;
- a candidate whose stated role is real but phrased outside the word list
  fails.

The lexicon's size is not the defect. **What it reads is.** Widening the
word list moves both failure modes in the wrong direction at once: it
admits more paraphrase while still not grounding anything.

The codebase already has the shape of the answer —
`_strip_ungrounded_expansions` grounds a candidate against `source_text` —
but applying it here is a design change, not a lexicon edit, and is filed
rather than smuggled in under a measurement.

## The candidate widening was scored, and is NOT shipped

`_CANDIDATE_ANCHOR_RE` in the probe (`estudiante`, `tesista`, `profesor`,
`in charge of`, `responsible for`, …) scored against all 9 stored runs:

```
caught (anchored):    4
missed (anchored):    8
FALSE POSITIVES:      0
stubs it could fail:  0
VERDICT: UNFALSIFIABLE
```

The zero is **not** a pass. A false positive can only come from a candidate
the shipped lexicon rejects, and every adjudicated stub in this corpus is
already admitted by the shipped lexicon via `participante` — so the
treatment was never handed anything it could get wrong. It also misses
twice as many anchored candidates as it catches, including every
`Representative from` and every AMI role.

Production is untouched, on the #613/#622 precedent: measured, and rejected
on its own numbers rather than adopted because the direction sounded right.

## Two things measured on the way past

**The participant pass leaks language, into the body as well as the title.**
`es-anchored` is 100 % Spanish. All three runs returned English
descriptions *and English bodies* — the body being a translated rendering
of the source's own Spanish turns (`I teach the distributed systems course
in the Department of Informatics`). #618/#630's gate covers titles on
chunked paths; nothing covers a translated body. `es-bare`, same pipeline,
stayed in Spanish, so this is not a constant.

**A 16 KB transcript hits production's own generation ceiling.** `en-ami`
run 1 failed with `OllamaGenerationCapped` at 8192 — the value
`openkos.yaml.template` ships — and run 3 came back with the judge skipped
and zero participant candidates. 1 of 3 runs of a real meeting transcript
is unusable at the shipped ceiling.

An earlier sweep at a 2048 ceiling was discarded rather than reported: that
was the probe's own invented number, and it capped 2 of 3 AMI runs. The
probe now mirrors the shipped ceiling so a failure here is a failure a real
`ingest` would have.

## What this probe cannot say

Three constructed arms are an **existence test**, not a population
estimate. "The lexicon misses `Representative from`" is supported. "N % of
real transcripts hit this" is not, and no run here licenses it.
