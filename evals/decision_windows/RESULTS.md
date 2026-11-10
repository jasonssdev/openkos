# Results: overlap and rolling subjects between decision-pass windows (#1738)

Registration: `PREREGISTRATION.md` in this directory. The owner delegated its items to the
author's recommendations on 2026-11-09, with three answers recorded: item 7 (n = 15, the
`both` arm not run), item 8 (a held-out confirmation before any default) and item 9 (the
gold SHA-256 pins, verified at load). It merged in PR #1747 (commit `a0d1a3d1`) before any
model call. The run used a detached checkout at `1e3334ec`, a later `main` whose only change
after `a0d1a3d1` is outside this directory. The store stamps the harness commit `1e3334ec`
with a clean tree, model `qwen3:8b` (digest
`500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`), generation ceiling
8192 and context window 12288. Prompt hashes are `extraction/decision_pass`
`6d3962b0dd75650c` (the shipped prompt) and `decision_windows/rolling_header`
`47a4b2f6997419ef`, both as registered. The parameters are as registered: an overlap head
of 6 lines and 800 characters, and a rolling list of 30 subjects. The stored file is
`windows-20261110T124138Z-qwen3-8b.json` in
`~/Dev/Projects/openkos-eval-runs/decision-windows/`, outside the repository.

## Verdict

**overlap: NOT SUPPORTED. rolling: NOT SUPPORTED.**

- **Overlap** fails bar G on all three series and bar K on TS3009. On ES2015 it recalls 0.6
  rows more than `shipped`, short of the 1.0 row and z >= 2.0 the bar asks. On TS3009 it
  recalls 1.3 rows fewer.
- **Rolling** fails bars G, K and F on all three series. It cuts the Decisions produced by
  roughly two thirds, and with them about half of the recall on ES2015 and IS1004 and nearly
  all of it on TS3009. Its window failures run 5 to 23 points above `shipped`.

Both results match the expectations stated before the run: overlap was likely to be small,
and rolling was likely to cut volume and recall.

## The run

Exposure, counted model-free before any call, was 9 gold `decision` rows at a window head,
above the floor of 6. The run covered 15 runs of 12 meetings for the three arms (540
meeting-runs, all present; `partial: false`). Wall time was about 12.6 h, inside the 15 h
ceiling: `shipped` took 4.75 h, `overlap` 3.61 h and `rolling` 4.24 h.

Window failures (meeting-runs whose pass failed), per arm and series:

| Arm | ES2015 | IS1004 | TS3009 | Kind |
|---|---|---|---|---|
| `shipped` | 4 of 60 | 1 of 60 | 9 of 60 | 10 capped (9 on TS3009), 4 unparseable replies |
| `overlap` | 2 of 60 | 1 of 60 | 1 of 60 | 3 unparseable replies, 1 capped |
| `rolling` | 13 of 60 | 6 of 60 | 23 of 60 | 32 unparseable replies, 10 capped |

## Bars

Each candidate against `shipped`, per series, at the shipped cap of 25. Wherever a z value
appears, it is the registered unpaired z.

| Bar | Arm | ES2015 | IS1004 | TS3009 |
|---|---|---|---|---|
| K recall kept (-1.0 row) | overlap | pass: 9.67 vs 9.07 | pass: 5.40 vs 5.53 | **fail**: 2.60 vs 3.87 |
| K | rolling | **fail**: 4.40 vs 9.07 | **fail**: 2.27 vs 5.53 | **fail**: 0.33 vs 3.87 |
| G gain | overlap | **fail**: +0.60 rows (z 1.14); precision x1.10 (z 1.92) | **fail**: -0.13 rows; precision x0.89 | **fail**: -1.27 rows; precision x0.73 |
| G | rolling | **fail**: precision x1.31 (z 2.55), but K fails | **fail**: precision x1.07 (z 0.62) | **fail**: precision x0.31 |
| V volume (x1.25) | overlap | pass: 62.7 vs 64.3 | pass: 49.1 vs 44.4 | pass: 79.7 vs 86.3 |
| V | rolling | pass: 24.3 | pass: 17.1 | pass: 23.2 |
| P guard (-0.05) | overlap | pass: 0.972 vs 0.924 | pass: 0.990 vs 1.000 | pass: 1.000 vs 1.000 |
| P | rolling | pass: 0.984 | pass: 0.950 | pass: 1.000 |
| F failures (+5 points) | overlap | pass: 0.033 vs 0.067 | pass: 0.017 vs 0.017 | pass: 0.017 vs 0.150 |
| F | rolling | **fail**: 0.217 | **fail**: 0.100 | **fail**: 0.383 |
| C calls (x1.15) | overlap | pass: 8.38 vs 8.52 | pass: 7.10 vs 7.07 | pass: 9.73 vs 9.75 |
| C | rolling | pass: 8.67 | pass: 7.27 | pass: 10.55 |

The verdict rule is mechanical: any `fail` on any series is NOT SUPPORTED.

## Arms

Mean per run (summed over the series' four meetings) at the cap of 25, from `--report`.

| Series | Arm | Recalled | Produced | Strict precision | Calls |
|---|---|---|---|---|---|
| ES2015 | shipped | 9.07 of 27 (6-13) | 64.3 | 0.141 | 34.1 |
| ES2015 | overlap | 9.67 of 27 (7-11) | 62.7 | 0.155 | 33.5 |
| ES2015 | rolling | 4.40 of 27 (2-8) | 24.3 | 0.185 | 34.7 |
| IS1004 | shipped | 5.53 of 27 (3-8) | 44.4 | 0.125 | 28.3 |
| IS1004 | overlap | 5.40 of 27 (4-7) | 49.1 | 0.111 | 28.4 |
| IS1004 | rolling | 2.27 of 27 (1-4) | 17.1 | 0.134 | 29.1 |
| TS3009 | shipped | 3.87 of 26 (2-6) | 86.3 | 0.045 | 39.0 |
| TS3009 | overlap | 2.60 of 26 (2-4) | 79.7 | 0.033 | 38.9 |
| TS3009 | rolling | 0.33 of 26 (0-1) | 23.2 | 0.014 | 42.2 |

Readings, not verdicts:

- The overlap effect sits inside the run-to-run swing. Its best series (ES2015, +0.6 rows)
  and its worst (TS3009, -1.3 rows) point in opposite directions, and the exposure of 9
  boundary rows over 12 meetings bounds what it could recover.
- Rolling makes the model list less, not more precisely. ES2015 is the one series where
  precision rises past the 1.25 ratio with z >= 2.0, and it pays for it with more than half
  of its recall.
- Rolling also makes the replies less parseable. With the list appended to each window, the
  failed meeting-runs rise from 4 to 13 on ES2015, 1 to 6 on IS1004 and 9 to 23 on TS3009, so
  part of its recall loss is windows lost outright.
- The `shipped` arm produces about 16, 11 and 22 Decisions per meeting on ES2015, IS1004
  and TS3009, in line with the volumes the registration expected. TS3009a's known runaway
  window shows up again as 9 capped meeting-runs.

## Deviations

- **The documented `--report` glob reads the checkpoints.** The harness writes a
  `windows-*-PARTIAL.json` checkpoint after each run, in the same directory as the final file.
  The registered command `--report $OUT/windows-*.json` therefore matched the 14 checkpoints
  as well as the final store. `--report` concatenates records without deduplicating them, so
  that reading counted each early run up to 15 times. It produced the same bars and the same
  two verdicts, but inflated volumes, for example a `shipped` ES2015 mean of 508.7 Decisions
  per run. The figures above come from `--report` on the final file alone, which holds all
  540 meeting-runs. No registered quantity changed. A fix belongs in the harness: either
  `--report` refuses duplicate (arm, run, meeting) records, or the checkpoints move out of the
  glob.
- Operational: the run used the `main` commit `1e3334ec` rather than `a0d1a3d1`. The
  commits between them touch only `evals/source_fallback/`, and the harness and
  registration files in this directory are byte-identical at both.

## What follows

- Neither treatment ships. The decision pass keeps its plain windows, and
  `transcript_decisions` stays NOT ADOPTED.
- On these series, the question #1738 raised has a negative answer. Only 9 gold rows sit at
  a window boundary, and overlap recovered none measurably, so boundary loss does not explain
  the pass's low recall. Any further recall work has to look at what the model emits inside
  a window.
- The rolling header should not be retried in this form. It costs recall and parse failures
  for a precision gain that held on one series only.
