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

Measured on `qwen3:8b`, 15 labelled edges, 5 runs per arm, same fixture:

| metric | baseline | `confidence` arm |
| --- | --- | --- |
| type accuracy vs label | 0.35 | 0.37 |
| mean stability | 0.99 | 0.96 |
| `related_to` share | 0.65 | 0.67 |
| mean run latency | 19.6s | 23.2s |

Two results, and the second one closed #508.

**The suggester fails its own rubric on roughly two thirds of decidable
pairs, and it does so with near-perfect stability.** It answers `related_to`
where a document says *"it happened because…"*, and `part_of` where a
document says *"one of the … each of them registered the same way"* — cases
the prompt's own tie-break chain says must resolve to `caused_by` and
`member_of`. Stability of 0.99 means it is not guessing: it is confidently,
reproducibly wrong. That is a far larger problem than automation ergonomics.

**Asking for a confidence is quality-neutral and buys a real but
insufficient signal.** Accuracy and distribution barely move, stability dips
slightly, latency rises ~18%. The confidence does carry signal — thresholding
lifts precision from 0.37 to 0.65 at `>=0.6` (27% of emissions admitted) and
to 0.73 at `>=0.9` (15% admitted) — but 0.73 means roughly **one auto-applied
relation in four is wrong by the rubric**. #385's concern was that bulk
acceptance "would rapidly apply a lot of low-value material"; writing wrong
types unattended at that rate is not an improvement on asking.

So no threshold gate shipped, and the confidence field was reverted rather
than left in production with no consumer. The harness stays: it is what makes
the next attempt measurable instead of hopeful.
