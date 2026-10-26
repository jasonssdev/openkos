# Second adjudication of the volatility-typing fixture (#1563)

The first author labelled the 27 items in `fixture.json`. The repository owner
then labelled all 27 blind (opaque shuffled ids, seed 1563; no first labels, no
flagged alternatives, no types or registry defaults, no reasoning), using the
sheet from `run_volatility_screen.py blind-sheet`. This file records the result,
the resolution of each disagreement and what it did to the floors. The filled
sheet is not committed (it is refused inside the repository by design).

`uv run python evals/volatility_typing_screen/run_volatility_screen.py adjudicate --sheet <filled sheet>`
compares a filled sheet with the **first** author's labels (`Item.first_label`),
so it keeps reproducing the numbers below after the fixture's `label` became the
resolved one; `--self-test` pins this.

## Agreement

| | |
| --- | --- |
| items | 27 |
| raw agreement | 19 / 27 = 0.704 |
| Cohen's kappa | 0.544 (moderate) |
| disagreements | 8 |

## Disagreements and their resolution

Every disagreement was resolved to the **second** adjudicator's label. The
first author's label stays in `fixture.json` as `alt_label` (so the item is
`ambiguous` and scored lenient on either), and the full record is the item's
`adjudication` object (`first`, `second`, `resolved`, `reason`).

| item | type | registry default | first | second | resolved | reason |
| --- | --- | --- | --- | --- | --- | --- |
| v01 | Concept | slow | slow | static | static | The second label is the first author's own flagged alternative; the owner chose it. |
| v02 | Decision | static | static | slow | slow | The second label is the first author's own flagged alternative; the owner chose it. |
| v12 | Concept | slow | slow | static | static | The second label is the first author's own flagged alternative; the owner chose it. |
| v17 | Procedure | volatile | volatile | slow | slow | The second label is the first author's own flagged alternative; the owner chose it. |
| v21 | Person | slow | slow | volatile | volatile | The second label is the first author's own flagged alternative; the owner chose it. |
| v25 | Concept | slow | slow | volatile | volatile | The second label is the first author's own flagged alternative; the owner chose it. |
| v26 | Decision | static | static | volatile | volatile | The encryption implementation is still pending ("Marcos lo implementa esta semana"), so its current status may change within days. |
| v27 | Procedure | volatile | volatile | slow | slow | The four-step procedure defines a stable operational process that is unlikely to change frequently. |

For six items (v01, v02, v12, v17, v21, v25) no per-item reasoning was given
beyond the owner's blanket decision to take the label the first author had
already flagged as defensible. v26 and v27 were not flagged alternatives in the
first pass (v26's alternative was `slow`, v27 had none); their reasons are the
owner's.

## The anchoring finding

In **all eight** disagreements the first author's label equalled the registry
default of the item's type. Two readings fit, and the screen cannot decide
between them:

1. The first author anchored on the registry (ADR-0007's own per-type defaults)
   when labelling, and the second adjudicator, who never saw the type, was not
   anchored.
2. The registry default is wrong for some types, and the blind adjudicator saw
   the content more accurately. Both `Procedure` items (v17, v27) are labelled
   `slow` by the owner against a `volatile` default.

The floors below are what follow from the resolved labels; they say nothing
about which reading is right.

## Effect on the floors

Recomputed from the committed fixture (`screen_core.registry_baseline`,
`screen_core.majority_baseline`):

| floor | first author's labels | resolved labels (decides) |
| --- | --- | --- |
| registry predictor, strict | 21 / 27 = 0.778 | **13 / 27 = 0.481** |
| registry predictor, lenient | 27 / 27 = 1.000 | 27 / 27 = 1.000 |
| commonest label | 12 / 27 = 0.444 (slow) | **11 / 27 = 0.407** (slow 11, static 9, volatile 7) |
| items where the registry is wrong (strict) | 6 | **14** |

The registry is now wrong on v01, v02, v03, v11, v12, v16, v17, v20, v21, v23,
v24, v25, v26 and v27. The lenient floor is uninformative: with the first
author's label kept as the alternative, the registry default matches one of the
two adjudicators' labels on every item.

Resolved class balance: 9 static, 11 slow, 7 volatile; 17 items carry a flagged
alternative.
