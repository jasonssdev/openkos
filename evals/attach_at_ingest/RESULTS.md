# Results: attach-at-ingest for near titles (#1701, part 2)

Registration: `PREREGISTRATION.md` in this directory, confirmed by the owner and merged in
PR #1733 (commit `f13d2e64`) before the held-out run. Harness commit stamped in the stored
file: `f13d2e64`, clean tree. Model: none (model-free; the stamp records a refused
connection). Rule hash `attach/rule` `a1bd519bb3057f91`. Held-out digest
`07bc8943cf6e7995a9e43f138bae2a10384699c5559d6aaaa099971136a8abc4`, as pinned. The run is
deterministic and was made once. Stored output:
`~/Dev/Projects/openkos-eval-runs/attach-at-ingest/attach-outcomes-heldout.json` (outside the
repository).

## Verdict

**REJECTED on B1.** `near_guarded` avoided 9 siblings against a bar of 10
(`max(8, ceil(0.25 x 40))`, gap 40). It added no wrong attachment (W1 holds) and attached
no Event or Person (W2 holds). Attach-at-ingest stays the shipped exact-title rule
(ADR-0045). Per the registration, a failed bar ends this registration; a new rule would need
a new one.

## Held-out population

134 candidates: 47 gold-same, 71 gold-distinct, 16 Event/Person. Gap (gold-same not attached
by the shipped rule) 40. Exposure: 25 distinct candidates exposed to the unguarded `near`
ablation across 4 classes; all floors met.

## Bars

| Bar | Threshold | `near_guarded` | Result |
|---|---|---|---|
| W1 wrong attachments added | 0 | 0 (upper 95% bound 0.042 of 71) | pass |
| W2 Event/Person attached | 0, every arm | 0 in every arm | pass |
| B1 siblings avoided | at least 10 | 9 (22.5% of the gap) | **fail** |

## Arms

| Arm | Right | Right added | Wrong | Wrong added |
|---|---|---|---|---|
| `shipped` | 7 | | 4 | |
| `near_guarded` | 16 | 9 | 4 | 0 |
| `near_loose` (ablation) | 18 | 11 | 7 | 3 |
| `near` (ablation) | 25 | 18 | 29 | 25 |

Per class, right/wrong/none:

| class | shipped | near_guarded | near_loose | near |
|---|---|---|---|---|
| ambiguous | 0/0/3 | 0/0/3 | 0/0/3 | 0/0/3 |
| concept-expanded | 0/0/6 | 0/0/6 | 0/0/6 | 6/0/0 |
| concept-reworded | 0/0/15 | 9/0/6 | 11/0/4 | 12/0/3 |
| cross-series | 0/2/0 | 0/2/0 | 0/2/0 | 0/2/0 |
| cross-type | 0/0/2 | 0/0/2 | 0/0/2 | 0/0/2 |
| decision-restated | 0/0/13 | 0/0/13 | 0/0/13 | 0/0/13 |
| deprecated-target | 0/0/4 | 0/0/4 | 0/0/4 | 0/1/3 |
| event | 0/0/8 | 0/0/8 | 0/0/8 | 0/0/8 |
| exact | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 |
| exact-homonym | 0/2/0 | 0/2/0 | 0/2/0 | 0/2/0 |
| imported-target | 0/0/3 | 0/0/3 | 0/0/3 | 0/0/3 |
| numeral-differs | 0/0/9 | 0/0/9 | 0/0/9 | 0/4/5 |
| part-whole | 0/0/9 | 0/0/9 | 0/0/9 | 0/7/2 |
| person | 0/0/8 | 0/0/8 | 0/0/8 | 0/0/8 |
| sibling-aspect | 0/0/10 | 0/0/10 | 0/0/10 | 0/0/10 |
| unreachable | 0/0/6 | 0/0/6 | 0/0/6 | 0/0/6 |
| unrelated | 0/0/8 | 0/0/8 | 0/0/8 | 0/0/8 |
| value-added | 0/0/9 | 0/0/9 | 0/2/7 | 0/7/2 |
| value-changed | 0/0/10 | 0/0/10 | 0/1/9 | 0/6/4 |

Wrong attachments added by `near_loose`: `h079` (value-changed, "Voltear el compost cada dos
semanas" onto the weekly decision), `h083` and `h088` (value-added, a restriction added to a
bike-lane width and to a support-channel decision).

## Misses of `near_guarded`

31 gold-same candidates were not attached: all 13 `decision-restated` (a Decision stated in
other words, e.g. "Release once a week" for the weekly-release decision), 6 of 15
`concept-reworded`, all 6 `concept-expanded` and all 6 `unreachable` (synonyms with no shared
words). A title rule cannot reach a restated Decision; the 9 it found are all reworded
Concepts.

## Reading

- The guards do what they were built for: 0 wrong of 71, where the unguarded rule adds 25.
- The benefit is too small to clear the registered bar, and it is concentrated in one class.
  Restated Decisions, the case #1701 describes for later meetings, are not reached at all.
- **Observation outside the bars.** The shipped exact-title rule attaches 4 wrong in every
  arm: 2 `cross-series` (an identical decision title from another group) and 2
  `exact-homonym`. In the fixture these titles are word-for-word copies and the schema
  carries no series field, so no title rule can separate them; whether real ingest does
  (it has provenance) is not measured here.

## Appendix: development set (decides nothing)

Observed while tuning, before the rule was frozen: `near_guarded` 0 wrong of 63 and 21 of 33
avoided; `near_loose` 8 wrong; `near` 20 wrong. The held-out avoided share (22.5%) is well
below the development one (64%), the expected sign of a rule tuned on its own data.
