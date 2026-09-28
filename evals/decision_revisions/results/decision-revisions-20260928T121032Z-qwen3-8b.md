# decision-revision-detector eval (#1014 piece (a), sub-change 3)

_Generated: 20260928T121032Z_

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
| true pairs proposed as a candidate (all) | 22 of 34 (0.65) |
| true pairs proposed as a candidate (original) | 14 of 24 (0.58) |
| true pairs proposed as a candidate (confirmation) | 8 of 10 (0.80) |
| missed true pairs | decisions/book-club-at-the-corner-cafe <-> decisions/book-club-venue, decisions/book-repair-kit-purchase <-> decisions/book-repair-kit-restock, decisions/childrens-story-time <-> decisions/toddler-read-aloud-session, decisions/drop-box-overnight-lock <-> decisions/returns-drop-box, decisions/fine-free-policy-review <-> decisions/no-charges-for-late-returns, decisions/first-saturday-book-swap-opening <-> decisions/saturday-closure, decisions/first-saturday-book-swap-opening <-> decisions/saturday-opening-hours, decisions/joining-cost <-> decisions/library-card-fee, decisions/large-print-book-section <-> decisions/large-print-book-section-labels, decisions/lost-item-replacement-billing <-> decisions/no-charges-for-late-returns, decisions/newsletter-send-day <-> decisions/volunteer-newsletter-frequency, decisions/no-charges-for-late-returns <-> decisions/overdue-fines |

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

## Judge -- stage (a), the real candidate set only

| metric | value |
| --- | --- |
| actionable rate (a) | 213 of 960 (0.22) |

## Relation this would write, per verdict (informational, S8/S9)

| verdict | relation |
| --- | --- |
| reverses | supersedes |
| refines | revises |
