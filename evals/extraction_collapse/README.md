# extraction_collapse — what collapses extraction to one object? (#522)

A matched-pair probe. Every fixture carries the same facts twice and varies
exactly one property, its **axis**. Both arms are extracted `--runs` times.
If the arm the hypothesis predicts will collapse does, and its twin holds,
the axis is implicated.

Two axes ship today:

| pair | axis | hypothesis |
| --- | --- | --- |
| `producto`, `versioning` | meeting register | a source recording a meeting collapses to the meeting (#522's original claim) |
| `anuncio` | whether the opening sentence enumerates the source's topics | a source that does not announce its own topics collapses regardless of register |

Each arm carries a **role** as well as a label: `TREATMENT` is the arm
predicted to collapse, `FLOOR` is the arm that has to hold. The verdict logic
reads roles, so a new axis needs no new harness.

```
uv run python -u evals/extraction_collapse/run_collapse_probe.py --self-test
uv run python -u evals/extraction_collapse/run_collapse_probe.py --runs 5
uv run python -u evals/extraction_collapse/run_collapse_probe.py --runs 5 --model qwen3:14b
uv run python -u evals/extraction_collapse/run_collapse_probe.py --runs 10 --union-judge
```

`--union-judge` runs `extract_concept_union` instead of the single-pass
`extract_concept`. `DEFAULT_UNION_JUDGE` is `True`, so **that flag is the
shipped configuration** — a number measured without it describes a path most
users never take.

`--self-test` makes no model calls and needs no Ollama.

## The positive control is pinned to a model AND a prompt

`TS3005b.summary.txt` is the case #522 was built on, and it runs unpaired so
a report of "no collapses" can be told apart from a probe that cannot see
one. Its premise — *this source collapses* — was measured under a specific
model and a specific prompt, so the note reads three ways:

| situation | what the report says |
| --- | --- |
| control collapses | sensitivity confirmed; other verdicts are about the fixtures |
| quiet, prompt unchanged | `SENSITIVITY UNCONFIRMED` — read no other verdict |
| quiet, prompt **changed** | the headline: this candidate moved the case #522 was built on |

That third row exists because the second one fired on the best result of the
day. The enumerate-first experiment stopped the control collapsing for the
first time ever, and the probe reported it as an instrument failure —
success and blindness were indistinguishable. The hash covers both prompt
channels, the system prompt and the user framing `_build_messages` applies,
since the language anchor moved only the second.

## Why it exists

[#522][522] requires that any fix be judged on **whether it fixes the
collapse**. When the issue was filed, nothing under `evals/` could measure
that:

- `extraction_cap/` holds expository prose that never collapses. It can
  witness a regression; it cannot show the defect was fixed.
- `decision_extraction/` runs on AMI meeting sources, which are gitignored,
  need a 22 MB download, and cost tens of KB per run.

So a prompt change would have been adopted against an adjacent metric — the
trap the loosened `Decision` rubric fell into, and the one a previous prompt
change already cost this project once.

## Why pairs, and not simply more meeting sources

The evidence table in #522 compares meeting material against unrelated
expository fixtures. Those differ in register **and** topic **and** length, so
the comparison cannot say which one collapses the output.

A pair holds the facts, the subjects and (within `MAX_LENGTH_SKEW`, checked
before any run) the length fixed, and varies only the register. What is left
to explain a difference is the framing.

## Reading the verdicts

| verdict | meaning |
| --- | --- |
| `AXIS IMPLICATED` | treatment arm collapsed, floor arm held. The finding. |
| `NO FLOOR` | **both** arms collapsed. The pair says nothing about its axis. |
| `NOT REPRODUCED` | neither arm collapsed at this model and size. |
| `INVERTED` | the floor arm collapsed and the treatment arm did not. |
| `NO RESULT` | an arm had no successful run. |

Every verdict names its axis, because "the treatment arm collapsed" is not
readable on its own once the probe carries more than one hypothesis.

An arm *collapses* when a **strict majority** of its successful runs return
exactly one object. Majority rather than mean, because the failure is
discrete — the model either enumerates the subjects or stops at the meeting —
and a mean lets one 5-object run hide two collapses. #522 records a source
yielding `1, 1, 5` across runs, so a single run is a sample, not a finding.

## What this does not measure

- **There is no target object count, and adding one would break it.** The
  flat arm's count is an affordance floor: evidence that *this text* holds
  more than one subject, produced by the extractor itself rather than
  asserted by a human. Scoring the meeting arm against it would be a scorer
  that rewards over-production — see the argument in
  `extraction_cap/run_cap_eval.py`.
- **`NO FLOOR` is not a quiet pass.** If both arms collapse, the honest
  reading is that the fixture affords nothing here, not that the meeting arm
  alone is a finding. The self-test fails if the code ever reports it as one.
- **Only short sources, only two pairs.** #522 observes collapse from 695 B
  to 40.8 KB. These fixtures sit at the small end, where a run is cheap. A
  pair at transcript scale is worth adding and is not here.
- **Constructed, not adjudicated.** Same limitation `edge_typing/fixtures.py`
  states: written to make one defect visible, not to certify behavior on
  organic material.

## Files

| file | what |
| --- | --- |
| `collapse_fixtures.py` | the matched pairs, and the length-skew guard |
| `run_collapse_probe.py` | the harness, its verdict logic, and `--self-test` |
| `report.md` | the canonical run, single-pass and union |

`collapse_fixtures.py` is deliberately not named `fixtures.py`: CI runs
`mypy .` over the whole repository and `edge_typing/fixtures.py` already
claims that top-level module name.

[522]: https://github.com/jasonssdev/openkos/issues/522
