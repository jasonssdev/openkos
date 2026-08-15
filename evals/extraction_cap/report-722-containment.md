# The #722 containment twin rule, measured — REJECTED

No model calls. Every number below comes from sweeps already on disk: 11
`results/runs-*.json` files (161 runs, 1038 retained objects) plus the
transcript probes that share `evals/participant_anchor`'s fixtures (43 runs,
169 objects).

**Reproduce:**

```bash
uv run python evals/extraction_cap/measure_title_containment.py            # floor 2
uv run python evals/extraction_cap/measure_title_containment.py --floor 3
uv run python evals/extraction_cap/measure_title_containment.py --self-test
```

## The proposal

`_drop_source_title_twins` compares a candidate's title to the source's own
through `_normalize_title` EQUALITY. #722 was filed on a candidate equality
cannot see: on `es-anchored` the extractor returned `Proyecto de memoria
institucional`, a string the source carries only inside its own title,
`Reunión de coordinación del proyecto de memoria institucional`. A
title-derived object — the class the twin rule exists to delete (#413 / #459 /
#522) — that the twin rule keeps.

The proposed repair was to widen "same title" from equality to containment,
above a token floor.

## The bar

Zero ground-truth subjects deleted, the bar #622 and #630 were held to — not
#699's recall/precision pair. `_drop_source_title_twins` DELETES, and a
wrongly deleted object is silent data loss with no recovery path. That is the
same argument the rule's own docstring makes for the `Procedure` exemption.

Exposure is printed beside every verdict, and the denominator is the variant's
own: a zero over a population the rule was never offered says nothing, which
`evals/participant_anchor` demonstrated once already by scoring a gate that had
discarded nothing in nine runs.

## Result

| variant | floor | eligible | reach | deletions | ground-truth subjects among them |
| --- | --- | --- | --- | --- | --- |
| containment | 1 | 1038 | 114 | 114 | **110** |
| containment | 2 | 1038 | 111 | 111 | **108** |
| containment | 3 | 1038 | 48 | 48 | **46** |
| containment-meeting | 1–3 | 52 | 0 | 0 | — (UNFALSIFIABLE) |

REJECTED at every floor. The floor is not the problem.

## Why it fails, and why the failure is structural

**A descriptive source title contains its subjects by construction.**

| source title | subject the rule would delete | deletions |
| --- | --- | --- |
| `Building a Research Agent with the Claude Agent SDK` | `Claude Agent SDK` | 46 |
| `Pre-built Skills, Skill Creator, and MCP Workflows` | `MCP Workflows` | 44 |
| `Pre-built Skills, Skill Creator y Workflows con MCP` | `Skill Creator` | 12 |
| `Building a Research Agent with the Claude Agent SDK` | `Research Agent` | 6 |

Against exactly one true positive, in 169 transcript objects:

| source title | object the rule would delete | deletions |
| --- | --- | --- |
| `Reunión de coordinación del proyecto de memoria institucional` | `Proyecto de memoria institucional` | 1 |

110 false positives to 1 hit. And the two cases are string-identical in shape —
a proper contiguous token subsequence of the source title in both. Containment
has no signal that separates them, because there is none in the strings.

Equality is safe precisely BECAUSE a twin restates the WHOLE title. That is
what makes it evidence of a lazy restatement emitted instead of doing the work.
Containment drops that requirement, and so fires on exactly the documents whose
titles do their job: naming what the document is about.

## It would reopen #413

`medium-08-sdk-skills`'s ground truth already documents this collision and the
escape that makes the current rule tolerable:

> It is conditional, not guaranteed: the comparison is exact after strip,
> casefold and whitespace collapse, so a model emitting `Building a Research
> Agent` or `Research Agent Construction` survives.

Containment closes that escape and deletes the exact strings that paragraph
names as survivors — `Building a Research Agent` (2) and `Research Agent` (6) —
plus `Claude Agent SDK` (46), which the same file lists as a genuine subject
surviving beside the `Procedure`.

#413 resolved that collision with a role exemption because the damage ran in
the wrong direction: a single-subject tutorial kept its primary object via the
floor, while a RICHER one lost it precisely BECAUSE it was richer. Containment
reintroduces exactly that, on every type the `Procedure` exemption does not
cover.

## The narrowing, and why it is not a pass

Restricting containment to meeting-shaped source titles
(`_MEETING_SHAPED_TITLE_RE`) removes every false positive above, because none
of those sources is meeting-shaped. It scores **UNFALSIFIABLE, not approved.**

Across both pools it was offered 217 eligible objects — 52 in the oracle corpus
(all of `medium-10-reunion-plataforma`) and 165 in the transcript probes — and
matched exactly one, the case #722 was filed on. That one hit sits in a pool
with no title-level ground truth, so nothing scores it either way.

A rule with one observation and no adjudicated verdict has not cleared a bar;
it has not been tested. Recorded here rather than filed as a follow-up, so the
next person to reach for it starts from the exposure figure instead of the
zero.

## What the defect actually costs, and where it does not live

One object in 170, on one fixture, burning one cap slot. Real, and small.

`_drop_framing_objects` does not own it either: that rule matches
`_MEETING_SHAPED_TITLE_RE` against the OBJECT's title, and `Proyecto de memoria
institucional` carries no gathering word. Neither deterministic rule can reach
this candidate, and the measurement above says no widening of the string
comparison can reach it either without costing subjects.

Production is unchanged.
