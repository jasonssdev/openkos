# decision-revision-detector eval (#1014 piece (a), sub-change 3)

_Generated: 20260928T033104Z_

Generation ceiling `8192` · context window `12288` · model `qwen3:8b` · **15 runs**.

Fixture: `evals/decision_revisions/revision_fixture_library.py` -- NOT AMI. Labels are owner-adjudicated (T3), never scored before settlement.

## Subject pass

| metric | value |
| --- | --- |
| subject produced | 42 of 42 (1.00) |
| evidence kept (of produced) | 42 of 42 (1.00) |
| pair overlap >= 0.5 (of pairs with both subjects) | 21 of 46 (0.46) |
| pairs excluded for a missing subject | 0 |

## Candidate-stage recall

| metric | value |
| --- | --- |
| true pairs proposed as a candidate | 15 of 24 (0.62) |
| missed true pairs | decisions/book-club-at-the-corner-cafe <-> decisions/book-club-venue, decisions/childrens-story-time <-> decisions/toddler-read-aloud-session, decisions/fine-free-policy-review <-> decisions/no-charges-for-late-returns, decisions/first-saturday-book-swap-opening <-> decisions/saturday-closure, decisions/first-saturday-book-swap-opening <-> decisions/saturday-opening-hours, decisions/joining-cost <-> decisions/library-card-fee, decisions/lost-item-replacement-billing <-> decisions/no-charges-for-late-returns, decisions/newsletter-send-day <-> decisions/volunteer-newsletter-frequency, decisions/no-charges-for-late-returns <-> decisions/overdue-fines |

## Direction accuracy (deterministic -- a lower number is a bug)

| metric | value |
| --- | --- |
| direction agrees with the fixture's own dates | 46 of 46 (1.00) |
| no-direction reasons | {'missing': 3, 'multiple': 2, 'equal': 4} |

## Judge -- stage (b), every labelled pair directly

| metric | value |
| --- | --- |
| REVERSES precision | 121 of 173 (0.70) |
| REVERSES recall | 121 of 165 (0.73) |
| REFINES precision | 68 of 84 (0.81) |
| REFINES recall | 68 of 120 (0.57) |
| REVERSES judged REFINES | 16 of 165 (0.10) |
| REFINES judged REVERSES | 37 of 120 (0.31) |
| actionable rate (b) | 257 of 690 (0.37) |
| mean pair stability (b) | 0.99 |

Confusion matrix (expected, observed) -> count:

```
REAFFIRMS  -> reaffirms : 75
REFINES    -> reaffirms : 15
REFINES    -> refines   : 68
REFINES    -> reverses  : 37
REVERSES   -> refines   : 16
REVERSES   -> reverses  : 121
REVERSES   -> unrelated : 28
UNRELATED  -> reaffirms : 15
UNRELATED  -> reverses  : 15
UNRELATED  -> unrelated : 300
```

## Judge -- stage (a), the real candidate set only

| metric | value |
| --- | --- |
| actionable rate (a) | 135 of 540 (0.25) |

## Relation this would write, per verdict (informational, S8/S9)

| verdict | relation |
| --- | --- |
| reverses | supersedes |
| refines | revises |
