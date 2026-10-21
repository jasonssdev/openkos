# Results: does the re-ask after a generation cap recover? (#1564)

Pre-registration: `PREREGISTRATION-1564.md` (commit `7d8bee24`, before any call).
Harness: `run_cap_reask.py` at commit `125dd57f`. Model `qwen3:8b`, digest
`500a1f067a9f`, run 2026-10-21, one Ollama, no other user. Stored trials (not
committed; failed calls carry a reply head from someone else's transcript):
`~/Dev/Projects/openkos-eval-runs/cap-reask-1564/cap-reask-20261021T024149Z-qwen3-8b.json`.

## Verdict (registered rule): INCONCLUSIVE, the production default stays

- Capped first asks: **15** (target reached; stop reason `target`), among 54 first
  asks (36 in the sweep, 18 in the replay).
- Recovered (capped, then a parseable non-capped reply): **5 of 15 = 0.333**,
  Wilson 95% **[0.152, 0.583]**.
- Rule: KEEP needs lower bound >= 0.30 (0.152, not met); SKIP needs upper bound
  < 0.30 (0.583, not met), or k = 0, or more than 250 s per recovered item (151.9 s,
  not met). So neither: INCONCLUSIVE.
- Re-ask cost: mean re-ask wall **151.9 s** (n = 15 re-asks). It is bimodal:
  the 5 recoveries took 6.6, 6.6, 9.3, 13.1 and 14.3 s; of the 10 failures, 9
  were a second full 8192-token runaway (230.6-343.7 s) and 1 was an unparseable
  reply (19.0 s).
  Items per recovered re-ask: 3.0 (3, 3, 3, 2, 4). Expected 1.0 item per re-ask,
  **151.9 s per recovered item**.
- Wall time of the whole measurement: 6195 s = **1.72 h** (forecast about 2.0 h,
  ceiling 2.5 h).

## What the data say, beyond the verdict

- **Every cap came from one window.** All 15 capped first asks are TS3009a
  window 4 (index from 0): 1 of 1 in the sweep (it capped twice) and 14 of 18 asks
  in the replay. The other 35 windows (ES2015d 9, TS3009b 11, TS3009d 10, TS3009a
  5) never capped in the sweep (0 of 35). So the rate is for one window of one
  meeting; the population-level cap rate is unmeasured by this design.
- That window's first ask caps in 15 of 19 asks (0.79), not the ~40% forecast. A
  non-capped ask on it yields 3-4 items (4 asks: 3, 4, 3, 4).
- The re-ask is a coin flip weighted toward a repeat runaway: when it recovers it
  is nearly free (under 15 s) and returns about the same 3 items an ordinary ask
  returns; when it does not, it costs a second runaway. Expected cost per cap
  151.9 s for 1.0 item.
- The stored-data inference (about 8 of 15 recovered, from wall time) was not
  confirmed: 5 of 15 here, and 8 of 15 lies inside this interval, so the two do
  not contradict each other either.

## Deviations from the pre-registration

- The cap-prone set was exactly one window, so the replay was a single-window
  loop, not a round-robin; the registered procedure covers that case.
- The first-ask cap rate on that window (0.79) was far above the forecast 40%,
  which is why the run finished under the forecast time.
- One re-ask failed as `unparseable` (not capped, 19.0 s); it counts as not
  recovered under the definition. Reported here so it is not read as a cap.
- RESULTS-only addition: the sample-size line below (not in the registration).

## What would settle it, and the production change if SKIP were chosen

At the observed rate (1/3), the interval's lower bound reaches 0.30 only at about
900 capped first asks (n = 60: 0.227; 150: 0.263; 300: 0.282; 600: 0.297; 900:
0.303), because the registered bar sits almost on the observed rate. More n on this
one window would not change the practical picture; a different population (windows
other than TS3009a w4, or another long meeting) would.

If a later registration chose SKIP, the production change is small: pass
`retry_after_cap=False` from `concept._run_decision_pass`
(`src/openkos/extraction/concept.py`, the single `run_decision_pass(windows, llm)`
call), with a spec requirement and an ADR note since it makes a capped window yield
nothing instead of a one-in-three recovery. **That change is not made here.**
