# Results: #1361, retrying a failed decision-pass window once

**Verdict: F now passes; NOT ADOPTED.** The retry brings window failures from
12 of 60 meeting-runs to 1 of 60, so bar F passes. Bar R still fails, narrowly,
so `transcript_decisions` stays off by default. The retry itself shipped in
#1362, behind the off-by-default key. Every rule applied below is the one fixed
in [`PREREGISTRATION-1361.md`](PREREGISTRATION-1361.md).

## Run

- Date: 2026-10-14. Code at fb15a627, identical to `main` after #1362.
- `qwen3:8b`, prompt hash `6d3962b0dd75650c`, AMI ES2015 a-d, 27 `decision`
  rows (1 unrecallable by construction, kept in the denominator).
- `pass` arm: 15 runs, 60 meeting-runs, exit 0, no uncaught error.
- `baseline`: reused from [`RESULTS-1348.md`](RESULTS-1348.md), as registered.

## Metrics (mean, min-max over 15 runs)

| Metric | `baseline` (#1348) | `pass`, #1348 | `pass` with retry |
|---|---|---|---|
| recall | 0.119 (0.000-0.222) | 0.311 (0.259-0.407) | 0.363 (0.259-0.481) |
| gold decisions recalled, of 27 | 3.2 (0-6) | 8.4 (7-11) | 9.8 (7-13) |
| Decisions per run | 5.1 (2-9) | 60.0 (53-68) | 63.5 (59-71) |
| matched_non_decision | 0.0 | 1.0 (0-2) | 1.1 (0-3) |
| unannotated | 2.7 (1-6) | 49.2 (44-57) | 51.2 (44-56) |
| precision-on-annotated | 1.000 | 0.908 (0.800-1.000) | 0.915 (0.800-1.000) |
| would merge at ingest | 0 | 0 | 0 |
| meeting-runs with a `failure`, of 60 | 0 | 12 | 1 |
| LLM calls per run | 41 | 33 | 33.6 (33-35) |
| wall time per run (s) | 1069.9 | 177.2 | 167.8 (138.3-376.2) |

Retries: 504 calls for 495 windows, so 9 windows were asked twice in 60
meeting-runs. The one remaining failure is an unparseable reply whose retry
was also unparseable (run 10, ES2015d).

## Bars

| Bar | Rule | Observed | Result |
|---|---|---|---|
| R | mean >= 0.369, and gap > larger spread | 0.363; gap 0.244 against spreads 0.222 and 0.222 | **FAIL** (first half, by 0.006) |
| P | mean >= 0.60, no run < 0.50 | 0.915, lowest 0.800 | pass |
| V | <= 81, mean and every run | 63.5, highest 71 | pass |
| F | <= 3 of 60, zero uncaught | 1 of 60, 0 uncaught | pass |
| D | <= 20% merge | 0% | pass |
| C | reported | +0.6 calls per run for retries (+1.8%) | reported |

## Reading

- The retry does what it was for: it fixes F at a cost of under 2% more calls.
- R misses by 0.006, less than one gold row (0.037). That is inside the noise
  this bar was written to exclude, so it is a fail by the registered rule and
  says nothing either way about a real difference.
- Recall rose from 0.311 to 0.363 between two separate 15-run arms. Part of that
  is plausibly the recovered windows; the two arms' ranges overlap, so the
  difference is not established.
- Still one series of four meetings. A further claim needs IS1004 and TS3009
  annotated, or a pre-registered change that targets recall.
