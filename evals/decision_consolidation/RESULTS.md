# Results: decision-pass consolidation and the MAX_PASS_DECISIONS cap (#1737)

Registration: `PREREGISTRATION.md` in this directory. The owner delegated its items to the
author's recommendations on 2026-11-09, with three answers recorded: item 3 (rank by support,
then window order, no English cue), item 8 (a held-out confirmation before any default) and
item 10 (report the thesis metric). It merged in PR #1744 (commit `9e300794`) before any model call. The
run used a detached checkout at that commit. The store stamps the harness commit `9e300794`
with a clean tree, model `qwen3:8b` (digest
`500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`), generation ceiling
8192, context window 12288 and merge cosine 0.75. Prompt hashes are
`extraction/decision_pass` `6d3962b0dd75650c` (the shipped prompt, as registered) and
`decision_consolidation/same_decision` `015e92802c6e3cd5`. The stored file is
`consolidation-20261109T191611Z-qwen3-8b.json` in
`~/Dev/Projects/openkos-eval-runs/decision-consolidation/`, outside the repository.

## Verdict

**NOT SUPPORTED.** Bar Q fails on all three series, bar V fails on ES2015 and TS3009, and bar
M fails on ES2015. Consolidation removes too few Decisions to raise strict precision, and on
ES2015 the merge it does make costs gold rows.

This matches the expectation stated before the run: most over-generation is not restatement
across windows, so a merge cannot reach the 1.5x precision bar at equal recall.

## The run

15 runs of 12 meetings (180 meeting-runs, all present; `complete: true`). Wall time was
4 h 49 min, inside the 9 h ceiling. 10 of 180 meeting-runs ended with a pass failure:

| Meeting | Failure | Count |
|---|---|---|
| TS3009a | `decision_pass: OllamaGenerationCapped` | 8 |
| ES2015c | `decision_pass: unparseable reply` | 1 |
| TS3009c | `decision_pass: unparseable reply` | 1 |

The registration reports the pass's own failure rate without gating it, because every arm
reads the same raw capture. The failures are not missing meeting-runs: each record is stored
with the items captured before the failing call.

## Bars

| Bar | ES2015 (human gold) | IS1004 (model gold) | TS3009 (human gold) |
|---|---|---|---|
| Q precision x1.5 | **fail**: 0.149 vs 0.154 (x0.97); 5 of 15 paired runs | **fail**: 0.125 vs 0.123 (x1.01); 15 of 15 | **fail**: 0.041 vs 0.038 (x1.07); 14 of 15 |
| K recall kept | pass: 8.60 vs 9.27 | pass: 5.47 vs 5.47 | pass: 3.33 vs 3.20 |
| M merge loses no gold | **fail**: 0.67 rows per run (bar 0.5); exposure 16 of 50 | vacuous: 0.00; exposure 0 of 9 | vacuous: 0.07; exposure 1 of 60 |
| V volume | **fail**: 57.9 per run, highest 67, limit 54 | pass: 43.7, highest 48, limit 54 | **fail**: 82.2, highest 88, limit 52 |
| P precision-on-annotated | pass: 0.910, lowest 0.800 | pass: 0.979, lowest 0.833 | pass: 1.000, lowest 1.000 |
| F guard failures | pass: 0 of 129 | pass: 0 of 48 | pass: 0 of 166 |
| C guard calls | pass: 2.1 per meeting-run vs 8.2 windows | pass: 0.8 vs 7.0 | pass: 2.8 vs 9.5 |

The verdict rule is mechanical: any `fail` on any series is NOT SUPPORTED. The two `vacuous`
M readings do not change it.

## Arms

Mean per run, strict precision and precision-on-annotated as defined in the registration.

| Series | Arm | Recalled | Decisions | Strict precision | Precision-on-annotated |
|---|---|---|---|---|---|
| ES2015 | shipped | 9.27 of 27 | 60.7 | 0.154 | 0.918 |
| ES2015 | rank_only | 9.27 of 27 | 60.7 | 0.154 | 0.918 |
| ES2015 | merge_only | 8.60 of 27 | 57.9 | 0.149 | 0.910 |
| ES2015 | consolidate | 8.60 of 27 | 57.9 | 0.149 | 0.910 |
| IS1004 | shipped | 5.47 of 27 | 44.2 | 0.123 | 0.979 |
| IS1004 | rank_only | 5.47 of 27 | 44.2 | 0.123 | 0.979 |
| IS1004 | merge_only | 5.47 of 27 | 43.7 | 0.125 | 0.979 |
| IS1004 | consolidate | 5.47 of 27 | 43.7 | 0.125 | 0.979 |
| TS3009 | shipped | 3.20 of 26 | 84.2 | 0.038 | 1.000 |
| TS3009 | rank_only | 3.20 of 26 | 84.2 | 0.038 | 1.000 |
| TS3009 | merge_only | 3.33 of 26 | 82.2 | 0.041 | 1.000 |
| TS3009 | consolidate | 3.33 of 26 | 82.2 | 0.041 | 1.000 |

Readings, not verdicts:

- The merge removes about 3 to 5% of Decisions (2.8 of 60.7 on ES2015, 0.5 of 44.2 on
  IS1004, 2.0 of 84.2 on TS3009). Q asked for about a third.
- `rank_only` equals `shipped` and `consolidate` equals `merge_only` on every series. The
  ranking changes which Decisions survive the cap only where the cap binds, and at these
  volumes per meeting it did not change the recalled count.
- On ES2015, 16 of the 50 items the merge absorbed matched a gold decision row. Bar M
  measures 0.67 rows per run lost to the merge, so in some of those merges the
  representative did not match the row its absorbed item did.

Time per meeting-run: pass 64.9 s, 52.5 s and 167.0 s (ES2015, IS1004, TS3009); consolidation
1.5 s, 0.7 s and 2.0 s.

## The cap recommendation (question 3)

Mean gold rows recalled on the consolidated set at each cap of the grid.

| Order | Series | 8 | 10 | 12 | 15 | 20 | 25 | 40 | none |
|---|---|---|---|---|---|---|---|---|---|
| ranked | ES2015 | 2.13 | 2.87 | 4.13 | 6.00 | 8.20 | 8.60 | 8.67 | 8.67 |
| ranked | IS1004 | 3.87 | 4.40 | 5.13 | 5.47 | 5.47 | 5.47 | 5.47 | 5.47 |
| ranked | TS3009 | 0.40 | 0.47 | 0.53 | 1.00 | 2.20 | 3.33 | 3.73 | 3.73 |
| positional | ES2015 | 1.93 | 2.80 | 4.00 | 6.00 | 8.07 | 8.60 | 8.67 | 8.67 |
| positional | IS1004 | 3.87 | 4.40 | 5.13 | 5.47 | 5.47 | 5.47 | 5.47 | 5.47 |
| positional | TS3009 | 0.27 | 0.40 | 0.60 | 1.07 | 2.20 | 3.33 | 3.73 | 3.73 |

The smallest grid cap within 0.5 gold row of no cap in every series is **25** in both orders,
the shipped value. The recommendation is to keep `MAX_PASS_DECISIONS` at 25. On TS3009 the cap
of 25 still costs 0.40 rows against no cap, inside the 0.5 tolerance. Ranked and positional
order differ by at most 0.2 rows at any cap, so the ranking buys nothing measurable here.

## Thesis metric (secondary, no bar)

The thesis's one-to-one `bge-m3` alignment at tau 0.70, read with `--report --thesis-metric`.

| Series | Arm | Recall | Precision |
|---|---|---|---|
| ES2015 | shipped | 0.148 | 0.066 |
| ES2015 | consolidate | 0.148 | 0.069 |
| IS1004 | shipped | 0.217 | 0.134 |
| IS1004 | consolidate | 0.217 | 0.136 |
| TS3009 | shipped | 0.233 | 0.072 |
| TS3009 | consolidate | 0.238 | 0.076 |

It agrees with the primary reading: consolidation moves precision by at most 0.004.

## Deviations

- None to the registered quantities, the arms or the bars.
- Operational: the first `--thesis-metric` call was made with `OLLAMA_HOST` pointed at a
  closed port, as the model-free `--report` command is, and failed with a connection error
  before printing anything. The registration documents that command without that variable,
  because it needs the embedding model. It was re-run as documented. The embedding calls ran
  while the #1738 run was using the chat model on the same Ollama server.

## What follows

- The consolidation step does not ship. `transcript_decisions` stays NOT ADOPTED and no
  OpenSpec change is opened for consolidation.
- `MAX_PASS_DECISIONS` stays 25, now with a measured reason.
- Precision on the decision pass is limited by Decisions that are not restatements (real
  decisions outside a sparse gold, and non-decisions). A merge cannot reach them; any further
  work on precision has to change what the pass emits, not deduplicate it afterwards.
