# Status: slices 1, 2 and 4 SHIPPED — slice 3 still blocked (2026-08-15)

## What has shipped

| Slice | State |
| --- | --- |
| 1 — volume eval | SHIPPED, PR #716. Its Phase 0.2 gate returned **REJECT** on D2. |
| 2 — reverse the stub rule | SHIPPED. The anchor gate is retired. |
| 3 — two-lane budget | **BLOCKED**, and now on stronger evidence than before. |
| 4 — advisory name grounding | SHIPPED. |

## What slice 1 decided, and what survived it

The Phase 0.2 gate REJECTed the D2 capture-prompt treatment: latency 1.92x
baseline (104.7s vs 54.6s) and merely-named count 0 vs 0. Per D2 a rejection
ships nothing prompt-level, so **task 2.4 is closed unshipped**. The rewrite
stays in `evals/named_person_volume/` as a reproducible monkeypatch.

Nothing else in the design was invalidated. D1/D3/D5/D6/D7 and all three
delta specs stood, and slices 2 and 4 are exactly D6 and D5.

## The owner ruling that unblocked slice 2

#712 carried an unresolved CONTRACT CONTRADICTION, stated in the issue itself:
`_PARTICIPANT_CAPTURE_SYSTEM_PROMPT` accepts "spoke in this meeting" as an
anchor, under which no speaker can ever be rejected, while D4/D5's stated
purpose is anti-flooding.

**Ruled: retire the anchor gate.** Identification grounds on the NAME appearing
in `source_text`, not on a role phrase in the model's own paraphrase.
Anti-flooding moves to the participant budget lane, which is where a volume
concern belongs — a lexicon was never going to carry it.

That ruling is what D6 and D5 already described, from opposite ends: D6 deletes
the gate, D5 adds the grounding that reads the source instead.

## Why slice 3 is still blocked, with better evidence

The two-lane budget exists to stop participants crowding subjects out of
`_UNION_BACKSTOP`. `evals/stage_attrition` (#715, PRs #717/#718) settled that
they are not competing at all:

- Baseline reproduces zero retained subjects on **9 of 9 runs**, three fixtures.
- `_extract_once` returns exactly ONE subject candidate per call on a
  meeting-shaped source — the Event naming the meeting — which
  `_drop_framing_objects` then correctly deletes.
- The backstop is 20 and only 3-5 objects are retained, so no capacity limit
  binds.

Subjects are not crowded out; **generation never produces them**. Building the
lane now would still fix a competition that is not happening.

That defect (#715) has its own authorized lever, and that lever is itself
blocked on #714 — a 16 KB transcript blows the 8192 generation ceiling once the
prompt asks for several objects. So the unblocking order is **#714 → #715 →
slice 3**, and slice 3 should not be started before the first two land.

## Re-entering for slice 3

Re-read `design.md` D3/D4 when the time comes. `_PARTICIPANT_BACKSTOP = 8`
(derived from `p_max = 3`, floor binding) is recorded in slice 1's report and
still unused. Note that D4 (lane truncation ordering) was resolved
*conditionally* and its condition should be re-checked against whatever #715's
fix does to the subject count — a lane ordering decided when subjects were
always zero has not been tested against the case it exists for.
