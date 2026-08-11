# extraction_collapse — what collapses extraction to one object? (#522)

A matched-pair probe. Every fixture carries the same facts twice and varies
exactly one property, its **axis**. Both arms are extracted `--runs` times.
If the arm the hypothesis predicts will collapse does, and its twin holds,
the axis is implicated.

Three axes ship today:

| pair | axis | hypothesis |
| --- | --- | --- |
| `producto`, `versioning` | meeting register | a source recording a meeting collapses to the meeting (#522's original claim) |
| `anuncio` | whether the opening sentence enumerates the source's topics | a source that does not announce its own topics collapses regardless of register |
| `lesson` | short lesson framing: an umbrella-topic title plus an opening sentence naming the lesson | a short titled lesson collapses to a single object echoing its own title, though its body covers three distinct sub-subjects |

Each arm carries a **role** as well as a label: `TREATMENT` is the arm
predicted to collapse, `FLOOR` is the arm that has to hold. The verdict logic
reads roles, so a new axis needs no new harness.

One fixture is **not** a pair: the negative control, where returning one
object is the *correct* answer. See below.

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

## The negative control measures the opposite failure

Every verdict above reads one object as a collapse. On a **genuinely
single-subject** source it is the right answer, and a candidate that returns
`[]` there has traded one defect for a worse one. No pair can express that —
a pair's floor arm is multi-subject by construction — so `NEGATIVE_CONTROL`
runs unpaired, exactly as the positive control does, and is reported in its
own section against `NEGATIVE_CONTROL_OBJECTS` instead of through a verdict.

| situation | what the report says |
| --- | --- |
| one object, and it is a **droppable** source-title twin | `no false positive` — the object stood on the floor and the floor held |
| one object, but a `Procedure` | `NO FLOOR EVIDENCE` — exempt by type, see below |
| one object, titled something else | `NO FLOOR EVIDENCE` — the rule had no candidate to drop |
| any run returned `[]` | `FALSE POSITIVE` — outranks every collapse verdict above it |
| more than one object | `SPLIT` — a different miss, reported separately so the two cannot be traded |
| every run errored | `NO RESULT` — a connection failure is not a false positive |

It is the `mcp-launch` shape named in `_drop_source_title_twins`: a title
naming one thing, a body about that one thing, whose lone object restates
the title. That object survives only because of the rule's floor (`len(...)
<= 1`, and the all-twins case), which is why a change to the twin rule is
exactly what this control exists to price. `--no-negative-control` skips it.

### A control that cannot fail has not passed

`_is_twin` exempts one type outright (#413):

```python
result.type != _TWIN_EXEMPT_TYPE and _normalize_title(result.title) == normalized_title
```

A `Procedure` is therefore **never** a twin, whatever its title — it would
survive with both floors deleted. The first negative control was a scheduled
job, came back as a `Procedure` in 5 of 5 live runs, and the probe reported
"no false positive" beside a number that did not depend on the floor at all.

So the fixture is now a definition rather than a how-to, and the harness no
longer trusts the type: `title_twin_runs` counts only droppable twins,
`exempt_twin_runs` counts the exempt ones, and either of the two
`NO FLOOR EVIDENCE` rows above says plainly that the run proves nothing. The
mirrored exempt-type constant is parity-checked against `concept.py` in
`--self-test`, because a stale mirror would quietly restore the false claim.

## The 1–4 KB band

`lesson` and the negative control are the only fixtures here written to a
**length** target: roughly 1–4 KB, the size a course lesson file is. Nothing
else under `evals/` sits in that band on single-topic material —
`extraction_cap/` is multi-subject expository prose at 7.6–17 KB, the three
meeting pairs are 600–800 B, and `decision_extraction/` runs on transcripts.
`measure_single_object_rate.py` states the consequence under
`SINGLE_SUBJECT_UNMEASURED`: a false-positive rate for the twin rule measured
without this band says nothing about the shape whose floor the rule protects.

The band is checked in `--self-test`, not assumed, for the same reason
`MAX_LENGTH_SKEW` is: a fixture that drifts out of it keeps reporting under
the same name while measuring something else.

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
- **Only short sources, only three pairs.** #522 observes collapse from 695 B
  to 40.8 KB. These fixtures sit at the small end, where a run is cheap. A
  pair at transcript scale is worth adding and is not here.
- **The `lesson` axis and the negative control carry no result yet.** Their
  first live run measured earlier versions of both fixtures and is what
  caused them to be rewritten; the text that ships now has not been run.
  Read them as unmeasured until a run says otherwise.
- **One negative control is one document.** A `[]` rate measured on it is
  evidence about that document, not a false-positive rate for the twin rule.
- **Constructed, not adjudicated.** Same limitation `edge_typing/fixtures.py`
  states: written to make one defect visible, not to certify behavior on
  organic material.

## Files

| file | what |
| --- | --- |
| `collapse_fixtures.py` | the matched pairs, the negative control, the length-skew guard and the 1–4 KB band |
| `run_collapse_probe.py` | the harness, its verdict logic, the negative-control note, and `--self-test` |
| `measure_single_object_rate.py` | how often `retained == 1` fires, from stored runs and zero model calls |
| `report.md` | the canonical run, single-pass and union — it predates `lesson` and the negative control |

`collapse_fixtures.py` is deliberately not named `fixtures.py`: CI runs
`mypy .` over the whole repository and `edge_typing/fixtures.py` already
claims that top-level module name.

[522]: https://github.com/jasonssdev/openkos/issues/522
