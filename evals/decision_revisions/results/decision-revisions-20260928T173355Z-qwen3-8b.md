# decision-revision-detector eval (#1014 piece (a), sub-change 3)

_Generated: 20260928T173355Z_

Generation ceiling `8192` · context window `12288` · model `qwen3:8b` · **15 runs**.

Fixture: `evals/decision_revisions/revision_fixture_library.py` -- NOT AMI. Labels are owner-adjudicated (T3), never scored before settlement. The `original` and `confirmation` splits (#1014 task T1) are always reported separately below, plus `all`; never blended.

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
| true pairs proposed as a candidate (all) | 21 of 34 (0.62) |
| true pairs proposed as a candidate (original) | 14 of 24 (0.58) |
| true pairs proposed as a candidate (confirmation) | 7 of 10 (0.70) |
| missed true pairs | decisions/book-club-at-the-corner-cafe <-> decisions/book-club-venue, decisions/book-repair-kit-purchase <-> decisions/book-repair-kit-restock, decisions/childrens-story-time <-> decisions/toddler-read-aloud-session, decisions/drop-box-overnight-lock <-> decisions/returns-drop-box, decisions/fine-free-policy-review <-> decisions/no-charges-for-late-returns, decisions/first-saturday-book-swap-opening <-> decisions/saturday-closure, decisions/first-saturday-book-swap-opening <-> decisions/saturday-opening-hours, decisions/joining-cost <-> decisions/library-card-fee, decisions/large-print-book-section <-> decisions/large-print-book-section-labels, decisions/lost-item-replacement-billing <-> decisions/no-charges-for-late-returns, decisions/newsletter-send-day <-> decisions/volunteer-newsletter-frequency, decisions/no-charges-for-late-returns <-> decisions/overdue-fines, decisions/seed-library-envelope-tracking <-> decisions/seed-library-envelope-tracking-review |

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
| REVERSES precision | 109 of 146 (0.75) |
| REVERSES recall | 109 of 165 (0.66) |
| REFINES precision | 75 of 93 (0.81) |
| REFINES recall | 75 of 120 (0.62) |
| REVERSES judged REFINES | 18 of 165 (0.11) |
| REFINES judged REVERSES | 30 of 120 (0.25) |
| actionable rate | 239 of 690 (0.35) |
| mean pair stability | 0.98 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 75
REFINES    -> reaffirms : 15
REFINES    -> refines   : 75
REFINES    -> reverses  : 30
REVERSES   -> refines   : 18
REVERSES   -> reverses  : 109
REVERSES   -> unrelated : 38
UNRELATED  -> reaffirms : 15
UNRELATED  -> reverses  : 7
UNRELATED  -> unrelated : 308
```

## Judge -- stage (b), original split, DIRECTED pairs only

| metric | value |
| --- | --- |
| REVERSES precision | 87 of 94 (0.93) |
| REVERSES recall | 87 of 120 (0.72) |
| REFINES precision | 75 of 93 (0.81) |
| REFINES recall | 75 of 90 (0.83) |
| REVERSES judged REFINES | 18 of 120 (0.15) |
| REFINES judged REVERSES | 0 of 90 (0.00) |
| actionable rate | 187 of 555 (0.34) |
| mean pair stability | 0.98 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 75
REFINES    -> reaffirms : 15
REFINES    -> refines   : 75
REVERSES   -> refines   : 18
REVERSES   -> reverses  : 87
REVERSES   -> unrelated : 15
UNRELATED  -> reverses  : 7
UNRELATED  -> unrelated : 263
```

## Judge -- stage (b), original split, UNDIRECTED pairs (change/no-change)

Expected REVERSES or REFINES = CHANGE; observed reverses or refines = change. The judge cannot reliably tell REVERSES from REFINES apart without an established direction, so an undirected verdict is scored only on whether a change was detected, never on which type.

| metric | value |
| --- | --- |
| change precision | 52 of 52 (1.00) |
| change recall | 52 of 75 (0.69) |
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
| REVERSES precision | 154 of 191 (0.81) |
| REVERSES recall | 154 of 210 (0.73) |
| REFINES precision | 165 of 183 (0.90) |
| REFINES recall | 165 of 210 (0.79) |
| REVERSES judged REFINES | 18 of 210 (0.09) |
| REFINES judged REVERSES | 30 of 210 (0.14) |
| actionable rate | 374 of 870 (0.43) |
| mean pair stability | 0.98 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 90
REFINES    -> reaffirms : 15
REFINES    -> refines   : 165
REFINES    -> reverses  : 30
REVERSES   -> refines   : 18
REVERSES   -> reverses  : 154
REVERSES   -> unrelated : 38
UNRELATED  -> reaffirms : 15
UNRELATED  -> reverses  : 7
UNRELATED  -> unrelated : 338
```

## Judge -- stage (b), all, DIRECTED pairs only

| metric | value |
| --- | --- |
| REVERSES precision | 132 of 139 (0.95) |
| REVERSES recall | 132 of 165 (0.80) |
| REFINES precision | 165 of 183 (0.90) |
| REFINES recall | 165 of 180 (0.92) |
| REVERSES judged REFINES | 18 of 165 (0.11) |
| REFINES judged REVERSES | 0 of 180 (0.00) |
| actionable rate | 322 of 735 (0.44) |
| mean pair stability | 0.99 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 90
REFINES    -> reaffirms : 15
REFINES    -> refines   : 165
REVERSES   -> refines   : 18
REVERSES   -> reverses  : 132
REVERSES   -> unrelated : 15
UNRELATED  -> reverses  : 7
UNRELATED  -> unrelated : 293
```

## Judge -- stage (b), all, UNDIRECTED pairs (change/no-change)

Expected REVERSES or REFINES = CHANGE; observed reverses or refines = change. The judge cannot reliably tell REVERSES from REFINES apart without an established direction, so an undirected verdict is scored only on whether a change was detected, never on which type.

| metric | value |
| --- | --- |
| change precision | 52 of 52 (1.00) |
| change recall | 52 of 75 (0.69) |
| undirected rows | 135 |

## Bars B1-B8 under the untyped-undirected rule (original split)

Thresholds are the SAME as this README's own bar table -- never moved by this rescoring. B3, B4, B6 and B7 read DIRECTED rows only; B1, B2, B5 and B8 are unaffected by the untyped-undirected rule and read exactly as the original bars do.

| Bar | Metric | Result | Verdict |
| --- | --- | --- | --- |
| B1 | Direction accuracy | 46 of 46 | pass |
| B2 | Candidate-stage recall | 14 of 24 | fail |
| B3 | REVERSES precision (directed) | 87 of 94 | pass |
| B4 | REVERSES recall (directed) | 87 of 120 | pass |
| B5 | REFINES precision (all rows, unaffected) | 75 of 93 | pass |
| B6 | REFINES recall (directed) | 75 of 90 | pass |
| B7 | REVERSES<->REFINES confusion (directed) | 18 of 210 | pass |
| B8 | Mean modal-verdict share (all rows, unaffected) | 0.98 | pass |

## Judge -- stage (a), the real candidate set only

| metric | value |
| --- | --- |
| actionable rate (a) | 200 of 960 (0.21) |

## Relation this would write, per verdict (informational, S8/S9)

| verdict | relation |
| --- | --- |
| reverses | supersedes |
| refines | revises |
