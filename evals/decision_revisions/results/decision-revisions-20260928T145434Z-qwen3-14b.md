# decision-revision-detector eval (#1014 piece (a), sub-change 3)

_Generated: 20260928T145434Z_

Generation ceiling `8192` · context window `12288` · model `qwen3:14b` · **15 runs**.

Fixture: `evals/decision_revisions/revision_fixture_library.py` -- NOT AMI. Labels are owner-adjudicated (T3), never scored before settlement. The `original` and `confirmation` splits (#1014 task T1) are always reported separately below, plus `all`; never blended.

## Subject pass

| metric | value |
| --- | --- |
| subject produced | 66 of 66 (1.00) |
| evidence kept (of produced) | 66 of 66 (1.00) |
| pair overlap >= 0.5 (of pairs with both subjects) | 32 of 58 (0.55) |
| pairs excluded for a missing subject | 0 |

## Candidate-stage recall

| metric | value |
| --- | --- |
| true pairs proposed as a candidate (all) | 24 of 34 (0.71) |
| true pairs proposed as a candidate (original) | 16 of 24 (0.67) |
| true pairs proposed as a candidate (confirmation) | 8 of 10 (0.80) |
| missed true pairs | decisions/book-club-at-the-corner-cafe <-> decisions/book-club-venue, decisions/childrens-story-time <-> decisions/toddler-read-aloud-session, decisions/fine-free-policy-review <-> decisions/no-charges-for-late-returns, decisions/guest-wifi-password-rotation <-> decisions/open-guest-network, decisions/joining-cost <-> decisions/library-card-fee, decisions/lost-item-replacement-billing <-> decisions/no-charges-for-late-returns, decisions/newsletter-send-day <-> decisions/volunteer-newsletter-frequency, decisions/no-charges-for-late-returns <-> decisions/overdue-fines, decisions/reference-desk-staffing <-> decisions/reference-desk-staffing-review, decisions/seed-library-envelope-tracking <-> decisions/seed-library-envelope-tracking-review |

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

## Judge -- stage (a), the real candidate set only

| metric | value |
| --- | --- |
| actionable rate (a) | 270 of 885 (0.31) |

## Relation this would write, per verdict (informational, S8/S9)

| verdict | relation |
| --- | --- |
| reverses | supersedes |
| refines | revises |
