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
