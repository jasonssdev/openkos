# Results: boosting the series a question names (#1702)

Registration: `PREREGISTRATION.md` in this directory, merged in PR #1724. Harness commit
stamped in the stored file: `48d0345e44dc68bd26afb44ad8130ddc91f57b57`, clean tree.
Embedder `bge-m3` (digest `7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab`).
No chat model was called. Prompt hashes: `answer/system` `e0ba5833e90054fa`,
`answer/sufficiency` `3e2b44fcf3e8477f`. The stored output is
`~/Dev/Projects/openkos-eval-runs/series-boost/contexts.json` (outside the repository).
The registered `--self-test` passed (82 checks) at that commit before `contexts` ran.

## Verdict

**REJECTED on C1** (the registered phase 1 futility stop). Phase 2 was not run, so C2 to C6
were not evaluated. The series boost stays off and nothing ships
([ADR-0086](../../docs/adr/0086-the-series-boost-stays-off-because-it-removed-too-few-foreign-blocks.md)
supersedes ADR-0085).

## Phase 1 (embedder only), 20 questions

| Bar | Measured | Threshold | Result |
|---|---|---|---|
| Exposure | 5 treated (c1-es, c2-es, e1-es, e2-es, e3-es) | at least 3 | pass |
| Headroom | 4 treated questions with a foreign block in `off` | at least 2 | pass |
| P1, nothing else moves | identity holds on the 15 untreated questions | byte-identical prompts | pass |
| P2, a wrong series never empties | `wrong` arm empty contexts: none | none | pass |
| **C1, foreign blocks fall** | 5 (`off`) to 3 (`boost`): a fall of 2, **40%** | at least 50% | **fail** |

Invariants: none violated. In the `wrong` arm (the other series boosted) the treated
questions carried 23 foreign blocks, against 5 in `off`: the reorder is real and works in
both directions, and it never emptied a context.

Per treated question, foreign blocks `off` to `boost` (of 8 delivered): e1-es 2 to 2,
e2-es 1 to 1, e3-es 1 to 0, c2-es 1 to 0, c1-es 0 to 0.

## Observation (not part of the verdict)

Reading the stored block ids, the boost reordered correctly but could not remove the three
remaining foreign blocks, because it only reorders the candidates retrieval already
returned and the named series had fewer of them than the 8 slots. In e1-es the council
series had 6 candidates in the list (4 Sources, one Decision, the shared `concepts/marea`),
so the other 2 slots stayed filled by engineering-series Decisions; in e2-es the
engineering series filled 7 slots and a council Source took the 8th. The two questions
that improved (e3-es, c2-es) are those where the named series had enough candidates to
displace the single foreign block.

Two details to read with care. `concepts/marea` is not neutral: it appears among the
boosted documents, which is consistent with it inheriting both series keys through
`provenance` (inferred from its position, the stored file does not record keys). And the
retrieval pool before the cut is not stored, so "fewer candidates than slots" is inferred
from the delivered ids, not read from the pool. No score was recomputed and no bar
reinterpreted.

## Cost

`contexts` took about 14 s on the embedder alone; no GPU hours were spent on a chat model
(forecast 0.05 h for this step).

## Deviations

None. The registered commands were run unchanged at the registered HEAD, and `decide` logic
and bars were not edited.

## Limits

- 5 treated questions (3 evolution, 2 control), so a change of one or two
  blocks moves the fall by 20 to 40 points; the result is a count, not an estimate.
- Synthetic Spanish titles of the shape `Name: date`, the shape the signal handles by
  construction; real titles were not measured.
- Two series, one product, one window (24576), the retrieval pool of 10 per channel.
- English variants naming the series were registered as untreated and were not recognised.
- It says this boost on this bundle was not licensed. It does not say mixing across series
  is absent or that no series signal could work.
