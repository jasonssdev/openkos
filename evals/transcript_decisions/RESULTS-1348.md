# Results: #1348, the transcript decision pass

**Verdict: NOT ADOPTED.** Two of the five gating bars fail (R and F), so
`transcript_decisions` stays off by default. The pass ships as an opt-in key, as
it already does on `main`. Every rule applied below is the one fixed in
[`PREREGISTRATION-1348.md`](PREREGISTRATION-1348.md); none was changed after
the run.

## Run

- Date: 2026-10-14. Code identical to `main` after #1358 (9843dd7a).
- Model `qwen3:8b`, decision pass prompt hash `6d3962b0dd75650c`,
  `MIN_VALUE_WORDS = 3`, `MAX_PASS_DECISIONS = 25`.
- AMI ES2015 a-d (development series only), 34 adjudicated rows, 27 of them
  `decision`; 1 of the 27 is unrecallable by construction (no act reaches the
  4-token match floor) and stays in the denominator.
- 15 runs per arm, 60 meeting-runs per arm. No uncaught error in either arm
  (both exited 0).

## Metrics (mean, min-max over 15 runs)

| Metric | `baseline` | `pass` |
|---|---|---|
| recall | 0.119 (0.000-0.222) | 0.311 (0.259-0.407) |
| gold decisions recalled, of 27 | 3.2 (0-6) | 8.4 (7-11) |
| Decisions per run | 5.1 (2-9) | 60.0 (53-68) |
| matched_decision | 2.4 (0-5) | 9.8 (8-13) |
| matched_non_decision | 0.0 (0-0) | 1.0 (0-2) |
| unannotated | 2.7 (1-6) | 49.2 (44-57) |
| precision-on-annotated | 1.000 (1.000-1.000) | 0.908 (0.800-1.000) |
| Decisions that would merge at ingest | 0.0 (0-0) | 0.0 (0-0) |
| meeting-runs with a `failure`, of 60 | 0 | 12 |
| LLM calls per run | 41 | 33 |
| wall time per run (s) | 1069.9 (985.2-1207.0) | 177.2 (132.3-367.1) |

`pass` failures by kind: 10 `decision_pass: unparseable reply`, 2
`decision_pass: OllamaGenerationCapped`. By meeting: ES2015d 7, ES2015c 4,
ES2015b 1, ES2015a 0. A failure is one window whose reply could not be used;
the meeting's other windows still yield their Decisions.

## Bars

| Bar | Rule | Observed | Result |
|---|---|---|---|
| R | gap >= 0.25, and gap > larger arm spread | gap 0.192; spreads 0.222 (baseline), 0.148 (pass) | **FAIL** (both halves) |
| P | mean >= 0.60, no run < 0.50 | 0.908, lowest run 0.800 | pass |
| V | <= 81 Decisions, mean and every run | 60.0, highest run 68 | pass |
| F | failure in <= 1 of 20 meeting-runs, zero uncaught errors | 12 of 60 (1 in 5); 0 uncaught | **FAIL** |
| D | <= 20% would merge at ingest | 0% | pass |
| C | reported only | +33 calls per run (+80% over baseline's 41) when the key is on | reported |

## Reading

- The pass recovers about 2.6 times as many hand-annotated decisions as today's
  extraction (8.4 against 3.2 of 27), with high precision on what the gold can
  judge. That is a real improvement, but smaller than the registered bar, and
  the baseline's own run-to-run spread (0 to 6 decisions) is as wide as the gap.
- The 3-run development figure for the same prompt was 0.407; the registered
  15-run mean is 0.311. The development runs chose the prompt, so they were
  optimistic by construction, as the registration said they would be.
- One window in five meeting-runs fails, almost always as a reply that does not
  parse. The failures concentrate on the later, longer meetings (c and d).
- This rests on one series of four meetings. It can reject adoption, which it
  does; it cannot certify a later variant.

## Follow-up candidates

Each needs its own registration before it can change the default:

- Recover failed windows (for example, one retry of an unparseable reply) and
  re-measure F.
- Raise recall without raising volume; the pass already produces about 49
  `unannotated` Decisions per run, which the gold cannot judge item by item.
- Annotate IS1004 and TS3009 so a result does not rest on ES2015 alone.
