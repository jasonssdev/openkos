# Pre-registration: #1231 (c), the twice-named new engineer

Written after the second baseline arm and BEFORE any treatment run of this
pass. Model `qwen3:8b`, generation ceiling 8192, context window 12288, the
real `extract_concept_union` over the synthetic fixtures in
`granularity_fixtures.py`, driven by `run_granularity.py`.

## Baseline

Two stored baseline arms of 15 runs per fixture, pooled to 30 runs per
fixture: `results/runs-baseline-20261002T020803Z-qwen3-8b.json` (#1256) and
`results/runs-baseline-20261006T034051Z-qwen3-8b.json` (this pass, current
main at 0.5.1).

Target, `en-review-new-engineer` person hit (`n of TOTAL`):
8 of 15 and 9 of 15, pooled 17 of 30 (0.567). Block spread over the six pooled
5-run blocks (0.6, 0.6, 0.4, 0.8, 0.4, 0.6): 0.40.

Failure analysis (the stored baseline outputs): every miss is a whole-run
collapse. The model returns exactly one `Event` ("Architecture review, 10
February"), `produced = 1`, the judge is skipped, and neither the Decisions
nor the Person is emitted. When the run does not collapse, the Person is
emitted. So (c) is the collapse-to-one-Event failure of #522 seen through the
Person lens, not a Person-specific recall gap.

## Candidates (all three registered now; each is one clause, applied by exact replacement)

- `role`: after the Person definition, "A newcomer introduced by role (for
  example joining a team as its second backend engineer) is such an
  individual, even when named only in a decision or staffing line."
- `attendees`: after "not five Person stubs", "; but a person the decisions
  or staffing lines are about (for example a new hire) is a subject, not an
  attendee."
- `newcomer`: after the Person definition, "(including a newcomer's role on a
  team)."

## Metrics

Target: twice-named new-engineer Person recall, `en-review-new-engineer`
`person_hit`, n of 15.

Guards, every metric the harness reports, per fixture, against the pooled
baseline (30 runs): topic recall, split rate, decisions per run, person hit
(`es-meeting-new-engineer`), person stubs per run, over-split of the control,
errored runs, mean latency.

## Adoption rule (fixed; not tuned after seeing a treatment)

Adopt a candidate only if ALL hold at n = 15 per arm and fixture:

1. Target: the arm's person-hit rate exceeds the pooled baseline rate by
   MORE than the baseline's own block spread (0.40) and by at least 0.25.
   Pooled 0.567 + 0.40 = 0.967, so only 15 of 15 qualifies. (This bar is
   deliberately strict; 14 of 15 is a gain of 0.367 and does not adopt.)
2. No guard drops below the pooled baseline minus the observed run-to-run
   spread, where the spread is the difference between the two baseline arms'
   values, floored at the harness rule's 0.05 for mean recall and 0.10 for
   rates:
   - `es-meeting-new-engineer`: recall >= 0.57; person hit >= 14 of 15;
     split >= 4 of 15.
   - `es-notes-5-decisions`: recall >= 0.93; split >= 12 of 15.
   - `es-gemini-notes-5-decisions`: recall >= 0.95; split >= 11 of 15.
   - `en-review-3-decisions`: recall >= 0.68; split >= 3 of 15.
   - `en-review-new-engineer`: recall >= 0.95 (its split rate has a 0.40
     baseline spread and a 0.27 baseline mean, so it cannot constrain).
   - `en-note-single-decision`: over-split <= 1 of 15; recall >= 0.95.
3. Person stubs per run rise by no more than 0.5 on any fixture; errored
   runs do not exceed baseline (0); mean latency per fixture stays under 1.5x
   the pooled baseline.
4. At most one candidate is adopted: the one that passes, and if several
   pass, the one with the shortest added text. If none passes, nothing ships
   and the negative result is the outcome.

Exposure: the target fixture fails in 13 of 30 pooled baseline runs, so the
treatment has room to move it; the guards each fail at least a few runs in
the baseline except `es-notes-5-decisions` split (2 of 30) and the control
(0 of 30), which therefore guard against regression only.
