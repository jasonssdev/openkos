# Results: llama.cpp vs Ollama on the same weights, with power (#1590)

**Verdict, by the registered rule: EQUIVALENT.** On `qwen3:8b` concept
extraction, `llama.cpp` and Ollama serving the same GGUF bytes are
equivalent on window coverage lift (TOST) and `llama.cpp` is non-inferior on
G1 precision; the window-failure veto did not fire and the full design
(k = 3, 330 window-runs per arm) completed. This is a statement about
extraction quality on this corpus, host and pair of runtime versions. It
licenses nothing by itself (see "What this does and does not license").
Refs #1590, part of #1556. No code, default, prompt or ADR changes.

## Registration and run

- Registered in [`PREREGISTRATION-1590.md`](PREREGISTRATION-1590.md) (merged in
  PR #1621, commit `12923467`). Metrics, margins, the decision rule, the
  design and the stopping rule are as registered; the statistics were
  computed once, by `decide`, after the last phase. No interim look.
- Phases, all completed in the registered order: `llama-5-6`,
  `judge-score-k2`, `ollama-da-3`, `llama-7`, `judge-score-k3`. Run started
  2026-10-27 00:03:37, last phase began 03:53:51.
- Measured model time **14,431 s (4.01 h)** against the 5.0 h budget and the
  4.59 h forecast, so the time rule for the third replicate never bound.
- Arms: Ollama `qwen3:8b` runs 1 to 3 (DA runs 1, 2 and F runs 1 to 3
  stored; DA run 3 new); `llama.cpp` runs 5 to 7 (all new, disjoint seeds).
  **330 window-runs per arm** (110 windows x 3 runs), **93 windows with
  gold** in the lift and G1 analysis.
- Harness errors: none. The logs record no abort, refusal or traceback.

## Result

| item | point | interval | registered bar | outcome |
| --- | ---: | --- | --- | --- |
| M1 window coverage lift, llama.cpp minus Ollama (TOST) | +0.0000066 | 90% [-0.00656, +0.00698] | inside (-0.016, +0.016) | **equivalent** |
| M2 G1 precision, llama.cpp minus Ollama (non-inferiority) | -0.0047 | one-sided 95% [-0.0416, +0.0311] | lower bound above -0.10 | **non-inferior** |
| V1 window failure rate, llama.cpp minus Ollama | -0.0030 | none (point rates below) | not more than +0.02 | **veto did not fire** |

M1 is the pooled mean over 93 windows of the per-window first-3-run mean
lift, two-stage bootstrap (meetings, then windows; 10,000 resamples, seed
42). The 95% interval, for reference only, is [-0.00779, +0.00846]. The
90% interval is about 0.43 of the margin on each side. The registered power
for this design was 0.843 (best case, assumptions in the registration); the
outcome is an equivalence, not an inconclusive result.

M2 is also reported against the two tighter, non-decisive bars:

| margin | outcome |
| ---: | --- |
| -0.10 (registered) | non-inferior |
| -0.08 | non-inferior |
| -0.05 (#1586's floor) | non-inferior (lower bound -0.0416) |

Final objects judged: 181 (`llama.cpp`) and 152 (Ollama). The registration
forecast about 200 per arm and stated that -0.05 could not be powered here
(547 objects per arm); the -0.05 reading is therefore a point-and-bound
report, not a powered test, and decides nothing.

## Window failures (V1)

| arm | failed window-runs | rate |
| --- | ---: | ---: |
| Ollama `qwen3:8b` | 24 of 330 | 0.0727 |
| `llama.cpp` `qwen3:8b` | 23 of 330 | 0.0697 |

Difference -0.0030 against the +0.02 veto margin. The counts are recovered
from the stored rates (rate x 330); the result file stores the rates.

## Timing (reported, never decisive)

| | median window-call time |
| --- | ---: |
| Ollama | 32.05 s |
| `llama.cpp` | 29.88 s |
| ratio, llama.cpp / Ollama | 0.93 |

Timing did not gate the verdict. It is a by-product of this run, on one
host; #1586
measured 31.9 s / 33.4 s. #1590's end-to-end speed bar is a separate
registration and is not tested here.

## Deviations from the registration

None found. The design, arms, seeds, windows, margins, bootstrap settings,
decision rule and stopping rule match the registration. Two things the
reader should know, neither a deviation:

- **Per-engine counts.** The result file does not store a per-engine
  objects-per-window-run figure or the Ollama run-to-run SD in pools DA and
  F, both listed as "reported" in the registration. They are not given here
  rather than reconstructed.
- **G1 object counts** (181 and 152) differ by about 19% between arms. The
  registration's non-inferiority test pools over objects and windows as
  registered; the difference in count is not itself a registered metric.

The run checkout's commit is not recorded in the result file; the harness
and prompts are those the registration pins (merge commit `12923467`).

## What this does and does not license

- **It licenses one statement:** on this corpus and host, `llama.cpp`
  0.7.0 (build 11669) serving the same `qwen3:8b` bytes with #1586's flags is
  not worse than Ollama 0.32.9 on extraction coverage lift (within +/-0.016)
  or on G1 precision (no loss beyond 0.10), and does not fail more windows.
- **It does not adopt `llama.cpp`.** Equivalence licenses nothing by itself.
  Adoption, or documenting `llama.cpp` as a supported opt-in, needs a
  separate ADR superseding ADR-0057, per epic #1556. The ADR is the
  maintainer's to open and merge.
- **It does not establish a speed benefit.** The 0.93 median-call ratio is
  not a bar. The end-to-end bar of #1590 (at least 15% less time to ingest
  a meeting or answer an Ask question, on the 32 GB floor emulation) is a
  separate registration that has not been run.
- **It does not cover** `gemma4:26b-a4b` (#1586 got identical output on the
  sufficiency arm), MLX, a default-f16-KV Ollama install (the comparison is
  against the operator's `q8_0` server, as registered), other hosts, or
  other versions of either runtime.
- **The G1 bar is weaker than #1586's.** The registration set -0.10 and
  asked the owner to confirm it is the right bar. The result clears -0.05
  too, but not as a powered test.

## Where the numbers live

Records, judgments, scores, `state.json` and `results-1590.json` are in
`~/Dev/Projects/openkos-eval-runs/equivalence-1590/`, outside the
repository, because they derive from the private extraction corpus. Only
aggregate numbers are recorded here.
