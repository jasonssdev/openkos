# Cross-series title collisions: results (#1736)

**Verdict: NOT EXPOSED.** Across 10 runs of the shipped decision pass on 12 AMI meetings, no
Decision shared the engine's attach key `(type, normalize_key(title))` with a Decision from
another meeting series. The mean is 0.000 cross-series collisions per run (min 0, max 0), against
the pre-registered EXPOSED bar of 1.0. No series guard is warranted by this measurement.

## Run identity

- Registration: `PREREGISTRATION.md`, merged in #1756 (19e8f855).
- Harness commit: 19e8f855, detached worktree, clean tree (0 modified files).
- Model: qwen3:8b through Ollama, one job at a time.
- Meetings: ES2015a-d, TS3009a-d, IS1004a-d; 10 runs; 120 meeting-runs.
- Wall clock: 11:10 to 14:32 EST on 2026-11-10, about 3 h 22 min, under the 4 h budget.
- Report read from the final store only (`collisions-20261110T161057Z-qwen3-8b.json`); the
  `-PARTIAL` checkpoint was not passed to `--report`.

## Per-run counts

| Run | Decisions | Cross-series | Within-series cross-meeting | Failed meeting-runs |
|---:|---:|---:|---:|---:|
| 1 | 203 | 0 | 0 | 0 of 12 |
| 2 | 189 | 0 | 0 | 1 of 12 |
| 3 | 188 | 0 | 0 | 2 of 12 |
| 4 | 182 | 0 | 0 | 1 of 12 |
| 5 | 188 | 0 | 0 | 0 of 12 |
| 6 | 177 | 0 | 0 | 2 of 12 |
| 7 | 198 | 0 | 0 | 0 of 12 |
| 8 | 203 | 0 | 0 | 0 of 12 |
| 9 | 197 | 0 | 0 | 1 of 12 |
| 10 | 195 | 0 | 0 | 1 of 12 |

Mean Decisions per run: 192.0. No run passed the exclusion bar of more than 2 failed
meeting-runs, so all 10 runs count (n = 10 of 10). There are no colliding titles to list.

Failed meeting-runs (8 of 120): 7 `OllamaGenerationCapped` (TS3009a in runs 2, 3, 4, 6, 9 and 10;
TS3009b in run 3) and 1 unparseable reply (ES2015c, run 6). This is the same TS3009a failure
pattern seen in #1737 and #1738.

## Exposure check (not registered, reported so the zero is not read as vacuous)

A zero can come from a counter that cannot match. Re-reading the final store with the same
`normalize_key`:

- The key does match repeated text: 335 of the 832 distinct keys recur across different runs of
  the same meeting, and 4 of 120 meeting-runs contain an exact duplicate inside one meeting.
- Pooling all 10 runs together, still 0 keys are shared between series.
- The median title is 12 words long. Titles come from the value statement, not the subject, so
  an exact cross-series match needs two teams to phrase a whole decision identically, even
  though all three AMI series share the same remote-control design scenario.

## What this means for #1736

- Question 1 (the attach rule can cross series) remains true; #1745 pins it.
- Question 2 (how often the shipped extractor triggers it) is answered for this corpus and model:
  not observed in 1,920 extracted Decisions. A series key promoted from `evals/series_boost` into
  the engine is not justified by measured harm.
- Limits: one model, one language, one meeting scenario, and Decisions only. Titles produced by
  other passes (shorter, subject-like titles) are not covered, and the homonym case stays a
  separate concern.
