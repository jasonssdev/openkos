# Results: the cap-exempt meeting Event and its body (#1689)

Registration: `PREREGISTRATION.md` (PR #1711, owner confirmations recorded in
its section "The owner confirms"), run unchanged. Run at commit
`5b918e3c8f0985a41eea98c8964f079be8b960ce` plus the `ADJUDICATION` registry
added by this change. Model for the compose phase: `qwen3:8b`, digest
`500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`
(`ollama list` id `500a1f067a9f`). Compose prompt hash
`fc7641ffe9df49d7`, language anchor `165db5afd66cf667`. Raw `score`/`decide`
output and every body stay outside the repository.

## Verdict

**REJECTED** (phase `composed`; failed bar: E5 faithful). Printed by
`decide --phase composed`: `verdict: REJECTED, failed: ["E5 faithful"]`.
The phase `natural` verdict was **NATURAL-INSUFFICIENT** (E1, E2, E4 failed),
which is what opened the GPU phase.

Placement (P1, P2) held. The composed body passed every deterministic bar but
was adjudicated unfaithful in 23 of 28 cases, almost always by naming
participants the source does not state. As registered, this licenses no
production change for the Event body. It does not undo the placement result
of #1693 (the cap-exempt placement is sound); it says this compose prompt on
this model does not produce a body that may be stored.

## Phase 0, `natural` (no GPU)

Both commands ran with `OLLAMA_HOST=127.0.0.1:1`.

| | Spanish | English |
|---|---|---|
| records | 75 | 254 |
| a0 non-empty | 67 | 244 |
| qualifies | 67 | 231 |
| beyond cap | 10 | 153 |
| displaced (P1) | 0 | 0 |
| bad cardinality (P2) | 0 | 0 |
| overflow (P2) | 0 | 0 |

Identical to #1693. P1 and P2 hold.

`e0` (the model's own body, recovered from the cache): 30 of 30 recovered,
30 scored, 0 unrecovered. restate pass 0 of 30 (mean 6-gram overlap 0.997:
the bodies are transcript dumps), linked 1 of 30, invented dates 0, bounded 2
of 30. E1, E2 and E4 fail: NATURAL-INSUFFICIENT. No adjudication (E1 to E4 did
not all hold).

## Phase 1, `composed` (GPU)

Phase 1a (10 cases): `futility_stop` printed `false`, read alone. Phase 1b ran
the remaining 20. 30 planned, 28 scored, 2 failed.

| Bar | Rule | Value | Result |
|---|---|---|---|
| P1 no displacement | 0 on both | 0 / 0 | holds |
| P2 cardinality, overflow | 0 on both | 0 / 0 | holds |
| E1 not a restatement | at least 0.90 | 28 of 28 (mean overlap 0.003) | holds |
| E2 linked | at least 0.80 | 23 of 28 (0.821) | holds |
| E3 invented date | at most 1 | 0 | holds |
| E4 bounded | at least 0.95 | 28 of 28 | holds |
| E5 faithful | at least 0.90 | 5 of 28 (0.179) | **fails** |
| E6 cost | failed at most 0.10; mean s at most 378.7 | 2 of 30 (0.067); 21.05 s | holds |

Floors: 28 scored (floor 20) and 2 of 30 failed (floor 15%): met.

Per source (scored / linked / faithful): ES2015a 6 / 6 / 3, ES2015d 6 / 5 / 0,
IS1004b 5 / 5 / 1, IS1004d 6 / 5 / 0, TS3009c 5 / 2 / 1. The failed calls were
`IS1004b-es/r5` and `TS3009c-es/r4` (no usable body; the call log reports no
transport failure). They count as not passing and were not re-run.

## Adjudication (disclosed)

**A delegated reader adjudicated, not the owner**: the agent that ran this
measurement, as the owner's standing instruction and the registration allow.
It read each body and description against the source (grepping the source for
each claimed participant, role label, object and the "next meeting in half an
hour" claim) before `decide` was first run for this phase, by key only. The
registry is `ADJUDICATION` in `measure_meeting_event_body.py`; each entry has a
one-line reason. The reader knew the arm (every scored body is `e1`).

- **faithful 5**: ES2015a r1, r5, r11; IS1004b r1; TS3009c r5.
- **unfaithful 23**. 19 name unnamed "other members of the team" (or
  "designers, managers") although each source has exactly four speakers
  (PM, ID, UI, ME). 4 (IS1004b r2, r3, r4, r6) use role labels the source never
  uses ("Product Manager", "President", "Executive Member", "User of
  Interface").
- contested 0.

Sensitivity, stated because the "other members" reading is the strict one: if
every "other members" body were excused, 24 of 28 would be faithful (0.857),
still below 0.90. The verdict does not depend on that call.

## Cost against the forecast

| | Forecast | Observed |
|---|---|---|
| Calls | 30 | 30 planned, 28 usable |
| Wall time, compose | 0.2 h low, 0.5 h typical, 2.1 h high | 228 s (1a) + 416 s (1b) = 644 s, 0.18 h |
| Mean per call | ~40 s (forecast basis) | 21.05 s |

Under the low end of the forecast and far under the 3 hour cap. Phase 0 cost
no GPU.

## Deviations and oddities

- **Run before the registration merged.** The registration says it becomes
  REGISTERED once the owner has confirmed it and the file has merged to
  `main`, before the first model call. This run used the confirmed
  registration at its pushed commit `5b918e3c` (PR #1711) before that PR merged.
  The commit was pushed and fixed before the first model call and was not
  edited afterwards, so the bars were not set after any result; the stated
  merge condition was still not met. The orchestrating session made this
  call, not the operator.

- The registry `ADJUDICATION` was added to the harness by this change, as the
  registration says it is filled after `score` (the sibling #1693 did the same).
- `score --phase composed` over all 30 cases prints `futility_stop: true`,
  because with 2 failed calls counted as not passing, E2 would need 24 passes
  of 30 and has 23. The registration reads that field only after phase 1a,
  where it was `false`, and E2 is defined over scored bodies (23 of 28), so it
  played no part in the verdict. Recorded so the printed field is not
  misread.
- The registration says to ask the owner for the GPU; the owner's instruction
  to this run stated the GPU was free. No other Ollama job ran.
- Nothing was re-run, subset or extended after a number was seen.

## Limits

As registered: Spanish bodies only, one corpus family, one model, one prompt,
one sample per case, correlated cases, exact-title links, and AMI transcripts
carry no date, so E3 mostly tests that no date is invented. The compose
prompt asks for participants "only by the names or role labels the source
uses" and still produced unnamed extra participants; with 4 speakers per
source the signal is consistent rather than noisy, but a different prompt or
model was not tried (tuning after a reply is a new registration).

## What the verdict licenses

Nothing for the Event body. The deterministic checks (restatement, linking,
dates, bounds) are met by a composed body; the faithfulness bar is not. A
follow-up would be a new registration, for example a composition that is given
the speaker roster from the source, or a post-check that rejects a body
naming participants the source lacks (a deterministic, model-free guard), and
the cap-exempt placement can ride on whichever body source passes.
