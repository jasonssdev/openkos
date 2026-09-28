# decision-revision-detector eval -- RESCORE (#1014 Plan 2, task T7: the untyped-undirected rule)

_Rescored from `runs-20260928T145434Z-qwen3-14b.json` -- zero model calls._

model `qwen3:14b`, 15 run(s), judge prompt `6de49030287f5d5b` (UNCHANGED by this rescore -- only the SCORING RULE for undirected pairs is new).

This recomputes the judge-stage, candidate-recall and direction-accuracy metrics from the stored raw verdicts under the untyped-undirected scoring rule. It cannot recompute the subject-pass section: that stage's raw per-Decision replies are not stored in `runs-*.json`.

## Candidate-stage recall

| metric | value |
| --- | --- |
| true pairs proposed as a candidate (all) | 24 of 34 (0.71) |
| true pairs proposed as a candidate (original) | 16 of 24 (0.67) |
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
| REVERSES precision | 105 of 157 (0.67) |
| REVERSES recall | 105 of 165 (0.64) |
| REFINES precision | 60 of 105 (0.57) |
| REFINES recall | 60 of 120 (0.50) |
| REVERSES judged REFINES | 45 of 165 (0.27) |
| REFINES judged REVERSES | 45 of 120 (0.38) |
| actionable rate | 262 of 690 (0.38) |
| mean pair stability | 0.99 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 75
REFINES    -> reaffirms : 15
REFINES    -> refines   : 60
REFINES    -> reverses  : 45
REVERSES   -> refines   : 45
REVERSES   -> reverses  : 105
REVERSES   -> unrelated : 15
UNRELATED  -> reaffirms : 15
UNRELATED  -> reverses  : 7
UNRELATED  -> unrelated : 308
```

## Judge -- stage (b), original split, DIRECTED pairs only

| metric | value |
| --- | --- |
| REVERSES precision | 90 of 112 (0.80) |
| REVERSES recall | 90 of 120 (0.75) |
| REFINES precision | 60 of 90 (0.67) |
| REFINES recall | 60 of 90 (0.67) |
| REVERSES judged REFINES | 30 of 120 (0.25) |
| REFINES judged REVERSES | 15 of 90 (0.17) |
| actionable rate | 202 of 555 (0.36) |
| mean pair stability | 0.99 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 75
REFINES    -> reaffirms : 15
REFINES    -> refines   : 60
REFINES    -> reverses  : 15
REVERSES   -> refines   : 30
REVERSES   -> reverses  : 90
UNRELATED  -> reverses  : 7
UNRELATED  -> unrelated : 263
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
| REVERSES precision | 150 of 202 (0.74) |
| REVERSES recall | 150 of 210 (0.71) |
| REFINES precision | 150 of 195 (0.77) |
| REFINES recall | 150 of 210 (0.71) |
| REVERSES judged REFINES | 45 of 210 (0.21) |
| REFINES judged REVERSES | 45 of 210 (0.21) |
| actionable rate | 397 of 870 (0.46) |
| mean pair stability | 0.99 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 90
REFINES    -> reaffirms : 15
REFINES    -> refines   : 150
REFINES    -> reverses  : 45
REVERSES   -> refines   : 45
REVERSES   -> reverses  : 150
REVERSES   -> unrelated : 15
UNRELATED  -> reaffirms : 15
UNRELATED  -> reverses  : 7
UNRELATED  -> unrelated : 338
```

## Judge -- stage (b), all, DIRECTED pairs only

| metric | value |
| --- | --- |
| REVERSES precision | 135 of 157 (0.86) |
| REVERSES recall | 135 of 165 (0.82) |
| REFINES precision | 150 of 180 (0.83) |
| REFINES recall | 150 of 180 (0.83) |
| REVERSES judged REFINES | 30 of 165 (0.18) |
| REFINES judged REVERSES | 15 of 180 (0.08) |
| actionable rate | 337 of 735 (0.46) |
| mean pair stability | 0.99 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 90
REFINES    -> reaffirms : 15
REFINES    -> refines   : 150
REFINES    -> reverses  : 15
REVERSES   -> refines   : 30
REVERSES   -> reverses  : 135
UNRELATED  -> reverses  : 7
UNRELATED  -> unrelated : 293
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
| B2 | Candidate-stage recall | 16 of 24 | fail |
| B3 | REVERSES precision (directed) | 90 of 112 | pass |
| B4 | REVERSES recall (directed) | 90 of 120 | pass |
| B5 | REFINES precision (all rows, unaffected) | 60 of 105 | fail |
| B6 | REFINES recall (directed) | 60 of 90 | pass |
| B7 | REVERSES<->REFINES confusion (directed) | 45 of 210 | fail |
| B8 | Mean modal-verdict share (all rows, unaffected) | 0.99 | pass |

## Judge -- stage (a), the real candidate set only

| metric | value |
| --- | --- |
| actionable rate (a) | 270 of 885 (0.31) |
