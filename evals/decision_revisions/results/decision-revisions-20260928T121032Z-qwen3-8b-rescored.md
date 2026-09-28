# decision-revision-detector eval -- RESCORE (#1014 Plan 2, task T7: the untyped-undirected rule)

_Rescored from `runs-20260928T121032Z-qwen3-8b.json` -- zero model calls._

model `qwen3:8b`, 15 run(s), judge prompt `5223c59f1d820c47` (UNCHANGED by this rescore -- only the SCORING RULE for undirected pairs is new).

This recomputes the judge-stage, candidate-recall and direction-accuracy metrics from the stored raw verdicts under the untyped-undirected scoring rule. It cannot recompute the subject-pass section: that stage's raw per-Decision replies are not stored in `runs-*.json`.

## Candidate-stage recall

| metric | value |
| --- | --- |
| true pairs proposed as a candidate (all) | 22 of 34 (0.65) |
| true pairs proposed as a candidate (original) | 14 of 24 (0.58) |
| true pairs proposed as a candidate (confirmation) | 8 of 10 (0.80) |

## Direction accuracy (deterministic -- a lower number is a bug)

| metric | value |
| --- | --- |
| direction agrees with the fixture's own dates (all) | 58 of 58 (1.00) |
| direction agrees with the fixture's own dates (original) | 46 of 46 (1.00) |
| direction agrees with the fixture's own dates (confirmation) | 12 of 12 (1.00) |
| no-direction reasons | {'missing': 3, 'multiple': 2, 'equal': 4} |

## Judge -- stage (b), original split

| metric | value |
| --- | --- |
| REVERSES precision | 108 of 153 (0.71) |
| REVERSES recall | 108 of 165 (0.65) |
| REFINES precision | 75 of 90 (0.83) |
| REFINES recall | 75 of 120 (0.62) |
| REVERSES judged REFINES | 15 of 165 (0.09) |
| REFINES judged REVERSES | 30 of 120 (0.25) |
| actionable rate | 243 of 690 (0.35) |
| mean pair stability | 1.00 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 75
REFINES    -> reaffirms : 15
REFINES    -> refines   : 75
REFINES    -> reverses  : 30
REVERSES   -> refines   : 15
REVERSES   -> reverses  : 108
REVERSES   -> unrelated : 42
UNRELATED  -> reaffirms : 15
UNRELATED  -> reverses  : 15
UNRELATED  -> unrelated : 300
```

## Judge -- stage (b), original split, DIRECTED pairs only

| metric | value |
| --- | --- |
| REVERSES precision | 90 of 105 (0.86) |
| REVERSES recall | 90 of 120 (0.75) |
| REFINES precision | 75 of 90 (0.83) |
| REFINES recall | 75 of 90 (0.83) |
| REVERSES judged REFINES | 15 of 120 (0.12) |
| REFINES judged REVERSES | 0 of 90 (0.00) |
| actionable rate | 195 of 555 (0.35) |
| mean pair stability | 1.00 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 75
REFINES    -> reaffirms : 15
REFINES    -> refines   : 75
REVERSES   -> refines   : 15
REVERSES   -> reverses  : 90
REVERSES   -> unrelated : 15
UNRELATED  -> reverses  : 15
UNRELATED  -> unrelated : 255
```

## Judge -- stage (b), original split, UNDIRECTED pairs (change/no-change)

Expected REVERSES or REFINES = CHANGE; observed reverses or refines = change. The judge cannot reliably tell REVERSES from REFINES apart without an established direction, so an undirected verdict is scored only on whether a change was detected, never on which type.

| metric | value |
| --- | --- |
| change precision | 48 of 48 (1.00) |
| change recall | 48 of 75 (0.64) |
| undirected rows | 135 |

## Judge -- stage (b), confirmation split

| metric | value |
| --- | --- |
| REVERSES precision | 45 of 45 (1.00) |
| REVERSES recall | 45 of 45 (1.00) |
| REFINES precision | 90 of 90 (1.00) |
| REFINES recall | 90 of 90 (1.00) |
| REVERSES judged REFINES | 0 of 45 (0.00) |
| REFINES judged REVERSES | 0 of 90 (0.00) |
| actionable rate | 135 of 180 (0.75) |
| mean pair stability | 1.00 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 15
REFINES    -> refines   : 90
REVERSES   -> reverses  : 45
UNRELATED  -> unrelated : 30
```

## Judge -- stage (b), confirmation split, DIRECTED pairs only

| metric | value |
| --- | --- |
| REVERSES precision | 45 of 45 (1.00) |
| REVERSES recall | 45 of 45 (1.00) |
| REFINES precision | 90 of 90 (1.00) |
| REFINES recall | 90 of 90 (1.00) |
| REVERSES judged REFINES | 0 of 45 (0.00) |
| REFINES judged REVERSES | 0 of 90 (0.00) |
| actionable rate | 135 of 180 (0.75) |
| mean pair stability | 1.00 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 15
REFINES    -> refines   : 90
REVERSES   -> reverses  : 45
UNRELATED  -> unrelated : 30
```

## Judge -- stage (b), confirmation split, UNDIRECTED pairs (change/no-change)

Expected REVERSES or REFINES = CHANGE; observed reverses or refines = change. The judge cannot reliably tell REVERSES from REFINES apart without an established direction, so an undirected verdict is scored only on whether a change was detected, never on which type.

| metric | value |
| --- | --- |
| change precision | 0 of 0 (0.00) |
| change recall | 0 of 0 (0.00) |
| undirected rows | 0 |

## Judge -- stage (b), all (never blended with the splits above)

| metric | value |
| --- | --- |
| REVERSES precision | 153 of 198 (0.77) |
| REVERSES recall | 153 of 210 (0.73) |
| REFINES precision | 165 of 180 (0.92) |
| REFINES recall | 165 of 210 (0.79) |
| REVERSES judged REFINES | 15 of 210 (0.07) |
| REFINES judged REVERSES | 30 of 210 (0.14) |
| actionable rate | 378 of 870 (0.43) |
| mean pair stability | 1.00 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 90
REFINES    -> reaffirms : 15
REFINES    -> refines   : 165
REFINES    -> reverses  : 30
REVERSES   -> refines   : 15
REVERSES   -> reverses  : 153
REVERSES   -> unrelated : 42
UNRELATED  -> reaffirms : 15
UNRELATED  -> reverses  : 15
UNRELATED  -> unrelated : 330
```

## Judge -- stage (b), all, DIRECTED pairs only

| metric | value |
| --- | --- |
| REVERSES precision | 135 of 150 (0.90) |
| REVERSES recall | 135 of 165 (0.82) |
| REFINES precision | 165 of 180 (0.92) |
| REFINES recall | 165 of 180 (0.92) |
| REVERSES judged REFINES | 15 of 165 (0.09) |
| REFINES judged REVERSES | 0 of 180 (0.00) |
| actionable rate | 330 of 735 (0.45) |
| mean pair stability | 1.00 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 90
REFINES    -> reaffirms : 15
REFINES    -> refines   : 165
REVERSES   -> refines   : 15
REVERSES   -> reverses  : 135
REVERSES   -> unrelated : 15
UNRELATED  -> reverses  : 15
UNRELATED  -> unrelated : 285
```

## Judge -- stage (b), all, UNDIRECTED pairs (change/no-change)

Expected REVERSES or REFINES = CHANGE; observed reverses or refines = change. The judge cannot reliably tell REVERSES from REFINES apart without an established direction, so an undirected verdict is scored only on whether a change was detected, never on which type.

| metric | value |
| --- | --- |
| change precision | 48 of 48 (1.00) |
| change recall | 48 of 75 (0.64) |
| undirected rows | 135 |

## Bars B1-B8 under the untyped-undirected rule (original split)

Thresholds are the SAME as this README's own bar table -- never moved by this rescoring. B3, B4, B6 and B7 read DIRECTED rows only; B1, B2, B5 and B8 are unaffected by the untyped-undirected rule and read exactly as the original bars do.

| Bar | Metric | Result | Verdict |
| --- | --- | --- | --- |
| B1 | Direction accuracy | 46 of 46 | pass |
| B2 | Candidate-stage recall | 14 of 24 | fail |
| B3 | REVERSES precision (directed) | 90 of 105 | pass |
| B4 | REVERSES recall (directed) | 90 of 120 | pass |
| B5 | REFINES precision (all rows, unaffected) | 75 of 90 | pass |
| B6 | REFINES recall (directed) | 75 of 90 | pass |
| B7 | REVERSES<->REFINES confusion (directed) | 15 of 210 | pass |
| B8 | Mean modal-verdict share (all rows, unaffected) | 1.00 | pass |

## Judge -- stage (a), the real candidate set only

| metric | value |
| --- | --- |
| actionable rate (a) | 213 of 960 (0.22) |
