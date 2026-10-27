# Adjudication of the decision-revisions fixture (#1563)

The drafter labelled the 58 pairs (`evals/decision_revisions/`), and the owner accepted the
16 the drafter flagged contested. On the owner's delegation a Claude model labelled all 58
blind with the sheet from `run_revisions_screen.py blind-sheet` (`second_labels.json`),
and, again on the owner's delegation, a third model (Claude Opus) resolved every
disagreement. **The owner labelled and resolved none of the second-round labels**; the
owner may override any resolution at registration.

## Agreement

| | |
| --- | --- |
| pairs | 58 |
| raw agreement | 55 / 58 = 0.948 |
| Cohen's kappa | 0.927 |
| disagreements | 3 (all score differently, all contested, all never right in the stored `qwen3:8b` run) |

`--self-test` reproduces these figures from `adjudication.json`.

## How the disagreements were resolved

Blind to annotator identity. For each pair the adjudicator read the two Decisions as the
sheet shows them (titles, bodies, dates, and which came first where the dates establish
it), the production judge prompt verbatim and the two candidate verdicts in sorted order.
It did not know which annotator gave which, did not read the drafter's notes, and looked
at no stored model run, result or score. Its verdicts were written down before the sources
were revealed. The adjudicated verdict could have been a third value; none was.

Each record is in `adjudication.json`, keyed by the sorted pair (`first`, `second`,
`resolved`, `reason`). A record may resolve to the drafter's label, which then stays the
fixture's label; `strict (1st)` keeps scoring the drafter's labels either way.

| item | pair | first | second | resolved | matched | reason |
| --- | --- | --- | --- | --- | --- | --- |
| r14 | `childrens-story-time` / `toddler-read-aloud-session` | REVERSES | UNRELATED | REVERSES | first | "moves to Thursday afternoons" presupposes the Wednesday-morning slot the earlier Decision set (the reason given is the Wednesday-morning nap), so the later overturns that choice. |
| r43 | `newsletter-schedule` / `volunteer-newsletter-frequency` | REFINES | REAFFIRMS | REAFFIRMS | second | The later Decision says no change was proposed and the newsletter continues monthly, restating the same choice; the first Monday is described as continuing practice, not a new condition. |
| r53 | `spring-book-sale` / `spring-book-sale-date` | UNRELATED | REAFFIRMS | REAFFIRMS | second | Both set the spring book sale for 25 April at the community hall; one restates the other's choice, and the doors-open time adds no condition to the choice. |

## What it did to the floors (`forecast`)

| | drafter's labels | adjudicated labels |
| --- | --- | --- |
| class balance (REVERSES / REFINES / REAFFIRMS / UNRELATED) | 14 / 14 / 6 / 24 | 14 / 13 / 8 / 23 |
| `lexical overlap (oracle cut)` (the reference for rule J) | 35 / 58 = 0.603 | 34 / 58 = 0.586 |
| `always unrelated` (the commonest label) | 24 / 58 = 0.414 | 23 / 58 = 0.397 |
| `always refines` | 17 / 58 = 0.293 | 16 / 58 = 0.276 |
| `always reverses` | 16 / 58 = 0.276 | 16 / 58 = 0.276 |
| `always reaffirms` | 6 / 58 = 0.103 | 8 / 58 = 0.138 |
| items wrong for the reference | 23 | 24 |
| items wrong for both `always unrelated` and the lexical cut | 19 | 21 |

The stored `qwen3:8b` scores (0.893, 0.887, 0.882 on the drafter's labels) are 0.928,
0.922 and 0.916 on the adjudicated ones, and the headroom above them is 6 items instead of
7; see `PREREGISTRATION-DRAFT.md`. They were re-scored from the stored rows
(`import-stored`, then `score`), not by this adjudication.
