# decision-revision-detector eval (#1014 piece (a), sub-change 3)

_Generated: 20260928T204537Z_

Generation ceiling `8192` · context window `12288` · model `qwen3:8b` · **15 runs**.

Fixture: `evals/decision_revisions/revision_fixture_library.py` -- NOT AMI. Labels are owner-adjudicated (T3), never scored before settlement. The `original` and `confirmation` splits (#1014 task T1) are always reported separately below, plus `all`; never blended.

Candidate mode: `embedding` (#1014 sub-change 3), embedding model `bge-m3`, similarity threshold `0.65` -- see `EMBEDDING_SIMILARITY_THRESHOLD`'s own docstring for its calibration basis and re-measurement requirement.

## Subject pass

| metric | value |
| --- | --- |
| subject produced | 66 of 66 (1.00) |
| evidence kept (of produced) | 66 of 66 (1.00) |
| pair overlap >= 0.5 (of pairs with both subjects) | 28 of 58 (0.48) |
| pairs excluded for a missing subject | 0 |

## Candidate-stage recall

| metric | value |
| --- | --- |
| true pairs proposed as a candidate (all) | 29 of 34 (0.85) |
| true pairs proposed as a candidate (original) | 19 of 24 (0.79) |
| true pairs proposed as a candidate (confirmation) | 10 of 10 (1.00) |
| missed true pairs | decisions/fine-free-policy-review <-> decisions/no-charges-for-late-returns, decisions/fine-free-policy-review <-> decisions/overdue-fines, decisions/guest-wifi-password-rotation <-> decisions/open-guest-network, decisions/lost-item-replacement-billing <-> decisions/no-charges-for-late-returns, decisions/no-charges-for-late-returns <-> decisions/overdue-fines |

## Direction accuracy (deterministic -- a lower number is a bug)

| metric | value |
| --- | --- |
| direction agrees with the fixture's own dates (all) | 58 of 58 (1.00) |
| direction agrees with the fixture's own dates (original) | 46 of 46 (1.00) |
| direction agrees with the fixture's own dates (confirmation) | 12 of 12 (1.00) |
| no-direction reasons | {'missing': 3, 'multiple': 2, 'equal': 4} |

Every split below reports the existing BLENDED four-way numbers unchanged (#1014 task T1), then the same rows restricted to DIRECTED pairs only (still four-way), then the UNDIRECTED pairs scored change/no-change only (#1014 Plan 2, task T7) -- an undirected verdict is never scored on WHICH type it named, only on whether it detected a change at all.

## Judge -- stage (b), original split

| metric | value |
| --- | --- |
| REVERSES precision | 106 of 144 (0.74) |
| REVERSES recall | 106 of 165 (0.64) |
| REFINES precision | 75 of 91 (0.82) |
| REFINES recall | 75 of 120 (0.62) |
| REVERSES judged REFINES | 16 of 165 (0.10) |
| REFINES judged REVERSES | 30 of 120 (0.25) |
| actionable rate | 235 of 690 (0.34) |
| mean pair stability | 0.98 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 75
REFINES    -> reaffirms : 15
REFINES    -> refines   : 75
REFINES    -> reverses  : 30
REVERSES   -> refines   : 16
REVERSES   -> reverses  : 106
REVERSES   -> unrelated : 43
UNRELATED  -> reaffirms : 15
UNRELATED  -> reverses  : 8
UNRELATED  -> unrelated : 307
```

## Judge -- stage (b), original split, DIRECTED pairs only

| metric | value |
| --- | --- |
| REVERSES precision | 89 of 97 (0.92) |
| REVERSES recall | 89 of 120 (0.74) |
| REFINES precision | 75 of 91 (0.82) |
| REFINES recall | 75 of 90 (0.83) |
| REVERSES judged REFINES | 16 of 120 (0.13) |
| REFINES judged REVERSES | 0 of 90 (0.00) |
| actionable rate | 188 of 555 (0.34) |
| mean pair stability | 0.98 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 75
REFINES    -> reaffirms : 15
REFINES    -> refines   : 75
REVERSES   -> refines   : 16
REVERSES   -> reverses  : 89
REVERSES   -> unrelated : 15
UNRELATED  -> reverses  : 8
UNRELATED  -> unrelated : 262
```

## Judge -- stage (b), original split, UNDIRECTED pairs (change/no-change)

Expected REVERSES or REFINES = CHANGE; observed reverses or refines = change. The judge cannot reliably tell REVERSES from REFINES apart without an established direction, so an undirected verdict is scored only on whether a change was detected, never on which type.

| metric | value |
| --- | --- |
| change precision | 47 of 47 (1.00) |
| change recall | 47 of 75 (0.63) |
| undirected rows | 135 |

## Judge -- stage (b), confirmation split

| metric | value |
| --- | --- |
| REVERSES precision | 45 of 46 (0.98) |
| REVERSES recall | 45 of 45 (1.00) |
| REFINES precision | 89 of 89 (1.00) |
| REFINES recall | 89 of 90 (0.99) |
| REVERSES judged REFINES | 0 of 45 (0.00) |
| REFINES judged REVERSES | 1 of 90 (0.01) |
| actionable rate | 135 of 180 (0.75) |
| mean pair stability | 0.99 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 15
REFINES    -> refines   : 89
REFINES    -> reverses  : 1
REVERSES   -> reverses  : 45
UNRELATED  -> unrelated : 30
```

## Judge -- stage (b), confirmation split, DIRECTED pairs only

| metric | value |
| --- | --- |
| REVERSES precision | 45 of 46 (0.98) |
| REVERSES recall | 45 of 45 (1.00) |
| REFINES precision | 89 of 89 (1.00) |
| REFINES recall | 89 of 90 (0.99) |
| REVERSES judged REFINES | 0 of 45 (0.00) |
| REFINES judged REVERSES | 1 of 90 (0.01) |
| actionable rate | 135 of 180 (0.75) |
| mean pair stability | 0.99 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 15
REFINES    -> refines   : 89
REFINES    -> reverses  : 1
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
| REVERSES precision | 151 of 190 (0.79) |
| REVERSES recall | 151 of 210 (0.72) |
| REFINES precision | 164 of 180 (0.91) |
| REFINES recall | 164 of 210 (0.78) |
| REVERSES judged REFINES | 16 of 210 (0.08) |
| REFINES judged REVERSES | 31 of 210 (0.15) |
| actionable rate | 370 of 870 (0.43) |
| mean pair stability | 0.99 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 90
REFINES    -> reaffirms : 15
REFINES    -> refines   : 164
REFINES    -> reverses  : 31
REVERSES   -> refines   : 16
REVERSES   -> reverses  : 151
REVERSES   -> unrelated : 43
UNRELATED  -> reaffirms : 15
UNRELATED  -> reverses  : 8
UNRELATED  -> unrelated : 337
```

## Judge -- stage (b), all, DIRECTED pairs only

| metric | value |
| --- | --- |
| REVERSES precision | 134 of 143 (0.94) |
| REVERSES recall | 134 of 165 (0.81) |
| REFINES precision | 164 of 180 (0.91) |
| REFINES recall | 164 of 180 (0.91) |
| REVERSES judged REFINES | 16 of 165 (0.10) |
| REFINES judged REVERSES | 1 of 180 (0.01) |
| actionable rate | 323 of 735 (0.44) |
| mean pair stability | 0.99 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 90
REFINES    -> reaffirms : 15
REFINES    -> refines   : 164
REFINES    -> reverses  : 1
REVERSES   -> refines   : 16
REVERSES   -> reverses  : 134
REVERSES   -> unrelated : 15
UNRELATED  -> reverses  : 8
UNRELATED  -> unrelated : 292
```

## Judge -- stage (b), all, UNDIRECTED pairs (change/no-change)

Expected REVERSES or REFINES = CHANGE; observed reverses or refines = change. The judge cannot reliably tell REVERSES from REFINES apart without an established direction, so an undirected verdict is scored only on whether a change was detected, never on which type.

| metric | value |
| --- | --- |
| change precision | 47 of 47 (1.00) |
| change recall | 47 of 75 (0.63) |
| undirected rows | 135 |

## Bars B1-B8 under the untyped-undirected rule (original split)

Thresholds are the SAME as this README's own bar table -- never moved by this rescoring. B3, B4, B6 and B7 read DIRECTED rows only; B1, B2, B5 and B8 are unaffected by the untyped-undirected rule and read exactly as the original bars do.

| Bar | Metric | Result | Verdict |
| --- | --- | --- | --- |
| B1 | Direction accuracy | 46 of 46 | pass |
| B2 | Candidate-stage recall | 19 of 24 | pass |
| B3 | REVERSES precision (directed) | 89 of 97 | pass |
| B4 | REVERSES recall (directed) | 89 of 120 | pass |
| B5 | REFINES precision (all rows, unaffected) | 75 of 91 | pass |
| B6 | REFINES recall (directed) | 75 of 90 | pass |
| B7 | REVERSES<->REFINES confusion (directed) | 16 of 210 | pass |
| B8 | Mean modal-verdict share (all rows, unaffected) | 0.98 | pass |

## Judge -- stage (a), the real candidate set only

| metric | value |
| --- | --- |
| actionable rate (a) | 318 of 690 (0.46) |

## Relation this would write, per verdict (informational, S8/S9)

| verdict | relation |
| --- | --- |
| reverses | supersedes |
| refines | revises |
