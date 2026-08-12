# `edge_typing` — the harness `suggest_edge_types` never had (#508)

Issue #508 asked for confidence-threshold auto-acceptance in `curate` and
named a cap-harness A/B as the gate on any prompt change. That gate did not
exist. `evals/extraction_cap/` scores **extraction**; nothing anywhere
scored this suggester, so a prompt change would have been adopted on
intuition — which this project has already paid for once.

This harness is that gate.

```bash
python evals/edge_typing/run_edge_typing_eval.py --arm baseline --runs 5
```

## What it measures

**Accuracy** against `fixtures.LabelledEdge.expected_type`. The labels are
**constructed, not adjudicated**: every document in `fixtures.py` was
written so the rubric in `edge_typing._RELATION_RUBRIC` has exactly one
defensible answer. Read this as rubric-consistency, not field accuracy — an
organic bundle carries ambiguity these pairs deliberately do not.

**Stability**, the modal type's share across runs. Needs no labels at all,
so it is the number to trust when a label is arguable. Same
self-contradiction logic as `extraction_cap/measure_acronym_fabrication.py`.

**Type distribution**. `related_to` is 67% of accepted edges on a real
bundle (`edge_typing.py:146`), and the rubric's stated aim is *not* to drive
that share down, so a sharp move in either direction is a finding to explain
before adopting.

**Calibration**, once an arm's prompt asks for a confidence: mean stated
confidence on correct answers against wrong ones. A threshold policy is only
meaningful if the second is clearly below the first.

## Never compare arms on different fixtures

The fixture set grew from 7 edges to 15 mid-investigation, and the first
baseline/treatment comparison spanned that change — which made it worthless.
`extraction_cap` records the same trap from the other direction: comparing
fixtures at different adjudication depths under-reported Spanish recall by
half. Re-run **both** arms whenever `fixtures.py` changes.

## What it found

All numbers below are `qwen3:8b` unless stated, on the **17-edge fixture**,
5 runs per arm (3 for the model sweep). Baseline was measured four times and
lands at **0.41–0.45**, so treat anything inside that band as noise.

### The model dominates everything else, and size dominates family

Same prompt, same fixture, only the model changed:

| model | accuracy | stability |
| --- | --- | --- |
| `gemma2:9b` | **0.63** | 0.92 |
| `qwen2.5:7b` | 0.55 | 0.80 |
| `llama3.1:8b` | 0.45 | 0.76 |
| `qwen3:8b` *(current default)* | 0.44 | 0.98 |
| `mistral:7b` | 0.27 | 0.92 |

The configured default is next to last, and **+0.37 is available from a
config value** — larger than any prompt change measured here.

Parameter count is the dominant axis, not family: `qwen3` 8b→14b is +0.22,
`gemma2` 9b→27b is +0.18. Family only separates sharply among the small
models, where the spread runs 0.27–0.63. Latency scales with it but stays
affordable — a 74-edge session is ~9 minutes on `gemma2:27b` against ~1.6 on
the default.

None of this is a safe swap, and the reason is measured rather than assumed.
`extraction_cap --runs 3` puts subject recall per fixture at:

| fixture | `qwen3:8b` | `qwen3:14b` | `gemma2:27b` |
| --- | --- | --- | --- |
| `large-03` (EN) | 0.81 | 0.81 | **0.24** |
| `medium-08` | 0.83 | 0.83 | **0.33** |
| `medium-09` | 0.83 | 0.75 | 0.58 |
| `small-04` (ES) | 0.76 | 0.52 | **0.00** |

**The best relation typer measured is the worst extractor measured.** The
`gemma2` family under-produces on extraction at both sizes — 3.3 objects per
run at 9b and 3.2 at 27b against the default's 6.8 — the opposite of how it
behaves here. So the +0.37 can only be collected by a per-task model (#515),
never by moving the default.

One trap worth naming, because it caught me: `qwen3:14b` produces 10.2 objects
per run against the default's 6.8, which reads as an upgrade and is not. The
extra volume is decay and unjudged titles — `medium-09`'s known-facet count
triples — so in that harness `produced` is not a quality signal. Its
adjudication debt also leaves its recall under-reported, so "worse" is not
established either.

### Three prompt arms, none of them shippable

| arm | accuracy | verdict |
| --- | --- | --- |
| baseline | 0.44 | — |
| few-shot | 0.51 | rejected, see below |
| less-priming | 0.41 | no effect |
| evidence-first | 0.29 | actively harmful |

**`evidence-first`** asked the model to quote the supporting sentence before
choosing, and pushed `related_to` from 0.65 to 0.93 of emissions. Naming the
abstention more often primed it — the failure `edge_typing.py` already
records from #388's era, where "a clause forbidding a shape made that shape
more frequent through priming".

**`less-priming`** removed two `related_to` mentions from the guard paragraph
while keeping its meaning. Nothing moved.

**`few-shot`** added six worked examples and reads as a +0.07 win. It is not
one. The entire gain sits on **2 of 17 edges**, both `member_of`, and both
phrased like the `member_of` example — *"one of the … like the van and the
truck"* against *"one of the scheduled maintenance jobs … each registered the
same way"*. The `member_of` pair written in deliberately different language
(*"a roster whose entries are peers"*) does not move, because baseline
already answers it. Six examples, one confusion moved, only where the surface
form matched: that is pattern-matching on phrasing the same author wrote on
both sides, not a model that learned the distinction. Rejected.

### What the suggester actually does

Precision per emitted type, baseline, 17 edges × 5 runs — "when it says T,
how often is T right":

| emitted | correct | emissions | precision |
| --- | --- | --- | --- |
| `member_of` | 6 | 6 | 1.00 |
| `produced_by` | 5 | 5 | 1.00 |
| `part_of` | 10 | 19 | 0.53 |
| `related_to` | 15 | 50 | 0.30 |
| `references` | 0 | 5 | **0.00** |

Two readings matter downstream:

**Specific types aggregate to 0.60.** That is exactly what `curate --accept
structure` writes unreviewed, so roughly two in five bulk-applied relation
types are wrong by the rubric. `curate` now says so once per accepted run.

**`related_to` at 0.30 is under-claiming, not caution.** Seven times in ten
it is emitted where the documents state a specific relationship — the case
the prompt's own tie-break (3) says must resolve to a specific type.

Stability of 0.98 across all of this means the suggester is not guessing. It
is confidently and reproducibly wrong, which rules out sampling-based
mitigations: majority voting and self-consistency both sample from the same
settled mistake, and #508 already measured and rejected a stated-confidence
threshold.

## What `s/edge` in a report does and does not tell you

The per-edge timing this harness prints is measured on **these fixtures**,
whose documents average 145 characters (94–197). Real bundles are larger,
and the cost per edge grows with them, because `_build_messages` puts the
**full body of both documents** into the prompt while the reply — a short
JSON object with a type and a rationale — stays roughly constant. It is the
input side that scales.

Measured on one machine, one model (`gemma2:27b`), one day:

| corpus | avg document | s/edge |
| --- | --- | --- |
| these fixtures | ~145 chars | 6.8 |
| `examples/good-life-demo` | ~1,220 chars | 11.5 |

An 8.4× larger input cost 1.70× the time. Re-running this harness on the
same machine reproduced 6.8, which is what rules out machine load as the
explanation rather than corpus size.

So: use this number to compare **models against each other**, which is what
the harness is for. Do not publish it as how long a real run takes without
saying what document size it was measured on — that mistake put an
optimistic figure in `docs/cli.md` (corrected since).

## Direction is not scored by the forward fixture — and the default model fails it completely (#561)

Every pre-#561 fixture edge presented the child/part/member/dependent as
SOURCE, so the eval scored *which type* while the *direction* was always
served correct. A model that ignores direction entirely could still score
0.81. Six reversed probes (collection → member, author → artifact, cause →
outcome) now reuse the same documents with the edge flipped;
`LabelledEdge.trap_type` records the asymmetric type that is only correct
the other way, and the runner reports **direction-trap hits** over the
reversed probes separately from accuracy.

Measured (3 runs per arm, 23-edge fixture):

| model / arm | forward-17 acc | trap hits (of 18) | reversed-6 acc |
| --- | --- | --- | --- |
| `gemma2:27b` baseline | 0.82 | **17 (0.94)** | 0.00 |
| `gemma2:27b` + direction-guard prompt | 0.75 | **17 (0.94)** | 0.00 |
| `qwen3:8b` baseline | 0.41 | 3 (0.17) | 0.17 |
| `qwen3:8b` + direction-guard prompt | 0.41 | 3 (0.17) | 0.17 |

Three findings:

**The production default is directionally blind.** `gemma2:27b` — packaged
for this task on #516's 0.81 — emits `member_of`, `produced_by`, and
`caused_by` backwards on essentially every reversed edge, at stability
1.00. Its 0.81/0.82 is real for type and silent about direction, and
direction is the property that determines whether the graph means anything
(a reversed `caused_by` asserts the migration was caused by the outage).

**The honest model and the accurate model are different models.**
`qwen3:8b` mostly answers `related_to` on reversed edges (3 trap hits) but
is far worse on type (0.41). Neither axis subsumes the other, and #516's
model sweep never saw the direction axis.

**A prompt guard does not carry the rule.** An explicit "Direction check"
paragraph (your type must be TRUE read as SOURCE → TARGET; if the claim
runs backwards, answer references/related_to) changed NOTHING on either
model's trap rate and cost `gemma2:27b` 0.07 forward accuracy — a strict
loss, rejected. This is the same tier-limitation the anti-twin rule hit
(`edge_typing.py`'s #380 comment): prose forbidding a shape does not
prevent it. The mitigation has to be deterministic — see the follow-up
issue filed from these numbers.
