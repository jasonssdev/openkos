# decision-revision-detector eval -- RESCORE (#1014 Plan 2, task T7: the untyped-undirected rule)

_Rescored from `runs-20260928T103525Z-qwen3-8b.json` -- zero model calls._

model `qwen3:8b`, 15 run(s), judge prompt `6de49030287f5d5b` (UNCHANGED by this rescore -- only the SCORING RULE for undirected pairs is new).

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
| REVERSES precision | 124 of 177 (0.70) |
| REVERSES recall | 124 of 165 (0.75) |
| REFINES precision | 67 of 81 (0.83) |
| REFINES recall | 67 of 120 (0.56) |
| REVERSES judged REFINES | 14 of 165 (0.08) |
| REFINES judged REVERSES | 38 of 120 (0.32) |
| actionable rate | 258 of 690 (0.37) |
| mean pair stability | 0.98 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 75
REFINES    -> reaffirms : 15
REFINES    -> refines   : 67
REFINES    -> reverses  : 38
REVERSES   -> refines   : 14
REVERSES   -> reverses  : 124
REVERSES   -> unrelated : 27
UNRELATED  -> reaffirms : 15
UNRELATED  -> reverses  : 15
UNRELATED  -> unrelated : 300
```

## Judge -- stage (b), original split, DIRECTED pairs only

| metric | value |
| --- | --- |
| REVERSES precision | 94 of 117 (0.80) |
| REVERSES recall | 94 of 120 (0.78) |
| REFINES precision | 67 of 81 (0.83) |
| REFINES recall | 67 of 90 (0.74) |
| REVERSES judged REFINES | 14 of 120 (0.12) |
| REFINES judged REVERSES | 8 of 90 (0.09) |
| actionable rate | 198 of 555 (0.36) |
| mean pair stability | 0.98 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 75
REFINES    -> reaffirms : 15
REFINES    -> refines   : 67
REFINES    -> reverses  : 8
REVERSES   -> refines   : 14
REVERSES   -> reverses  : 94
REVERSES   -> unrelated : 12
UNRELATED  -> reverses  : 15
UNRELATED  -> unrelated : 255
```

## Judge -- stage (b), original split, UNDIRECTED pairs (change/no-change)

Expected REVERSES or REFINES = CHANGE; observed reverses or refines = change. The judge cannot reliably tell REVERSES from REFINES apart without an established direction, so an undirected verdict is scored only on whether a change was detected, never on which type.

| metric | value |
| --- | --- |
| change precision | 60 of 60 (1.00) |
| change recall | 60 of 75 (0.80) |
| undirected rows | 135 |

## Judge -- stage (b), confirmation split

| metric | value |
| --- | --- |
| REVERSES precision | 45 of 60 (0.75) |
| REVERSES recall | 45 of 45 (1.00) |
| REFINES precision | 75 of 75 (1.00) |
| REFINES recall | 75 of 90 (0.83) |
| REVERSES judged REFINES | 0 of 45 (0.00) |
| REFINES judged REVERSES | 15 of 90 (0.17) |
| actionable rate | 135 of 180 (0.75) |
| mean pair stability | 1.00 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 15
REFINES    -> refines   : 75
REFINES    -> reverses  : 15
REVERSES   -> reverses  : 45
UNRELATED  -> unrelated : 30
```

## Judge -- stage (b), confirmation split, DIRECTED pairs only

| metric | value |
| --- | --- |
| REVERSES precision | 45 of 60 (0.75) |
| REVERSES recall | 45 of 45 (1.00) |
| REFINES precision | 75 of 75 (1.00) |
| REFINES recall | 75 of 90 (0.83) |
| REVERSES judged REFINES | 0 of 45 (0.00) |
| REFINES judged REVERSES | 15 of 90 (0.17) |
| actionable rate | 135 of 180 (0.75) |
| mean pair stability | 1.00 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 15
REFINES    -> refines   : 75
REFINES    -> reverses  : 15
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
| REVERSES precision | 169 of 237 (0.71) |
| REVERSES recall | 169 of 210 (0.80) |
| REFINES precision | 142 of 156 (0.91) |
| REFINES recall | 142 of 210 (0.68) |
| REVERSES judged REFINES | 14 of 210 (0.07) |
| REFINES judged REVERSES | 53 of 210 (0.25) |
| actionable rate | 393 of 870 (0.45) |
| mean pair stability | 0.99 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 90
REFINES    -> reaffirms : 15
REFINES    -> refines   : 142
REFINES    -> reverses  : 53
REVERSES   -> refines   : 14
REVERSES   -> reverses  : 169
REVERSES   -> unrelated : 27
UNRELATED  -> reaffirms : 15
UNRELATED  -> reverses  : 15
UNRELATED  -> unrelated : 330
```

## Judge -- stage (b), all, DIRECTED pairs only

| metric | value |
| --- | --- |
| REVERSES precision | 139 of 177 (0.79) |
| REVERSES recall | 139 of 165 (0.84) |
| REFINES precision | 142 of 156 (0.91) |
| REFINES recall | 142 of 180 (0.79) |
| REVERSES judged REFINES | 14 of 165 (0.08) |
| REFINES judged REVERSES | 23 of 180 (0.13) |
| actionable rate | 333 of 735 (0.45) |
| mean pair stability | 0.99 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 90
REFINES    -> reaffirms : 15
REFINES    -> refines   : 142
REFINES    -> reverses  : 23
REVERSES   -> refines   : 14
REVERSES   -> reverses  : 139
REVERSES   -> unrelated : 12
UNRELATED  -> reverses  : 15
UNRELATED  -> unrelated : 285
```

## Judge -- stage (b), all, UNDIRECTED pairs (change/no-change)

Expected REVERSES or REFINES = CHANGE; observed reverses or refines = change. The judge cannot reliably tell REVERSES from REFINES apart without an established direction, so an undirected verdict is scored only on whether a change was detected, never on which type.

| metric | value |
| --- | --- |
| change precision | 60 of 60 (1.00) |
| change recall | 60 of 75 (0.80) |
| undirected rows | 135 |

## Bars B1-B8 under the untyped-undirected rule (original split)

Thresholds are the SAME as this README's own bar table -- never moved by this rescoring. B3, B4, B6 and B7 read DIRECTED rows only; B1, B2, B5 and B8 are unaffected by the untyped-undirected rule and read exactly as the original bars do.

| Bar | Metric | Result | Verdict |
| --- | --- | --- | --- |
| B1 | Direction accuracy | 46 of 46 | pass |
| B2 | Candidate-stage recall | 14 of 24 | fail |
| B3 | REVERSES precision (directed) | 94 of 117 | pass |
| B4 | REVERSES recall (directed) | 94 of 120 | pass |
| B5 | REFINES precision (all rows, unaffected) | 67 of 81 | pass |
| B6 | REFINES recall (directed) | 67 of 90 | pass |
| B7 | REVERSES<->REFINES confusion (directed) | 22 of 210 | fail |
| B8 | Mean modal-verdict share (all rows, unaffected) | 0.98 | pass |

## Judge -- stage (a), the real candidate set only

| metric | value |
| --- | --- |
| actionable rate (a) | 225 of 915 (0.25) |
