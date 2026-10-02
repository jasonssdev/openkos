# contradiction-judge eval — arm `baseline` (#558)

_Generated: 20261002T010540Z_ · model `qwen3:8b` · **15 runs** over 0 labelled pairs.

Generation ceiling `8192` · context window `12288`.

Labels are CONSTRUCTED, not adjudicated — see `contradiction_fixtures.py`.

**`--merged-only` run: the typed-edge rows below measured nothing (0 pairs) and read 0.00 by construction; only the merged-content rows carry a result.**

| metric | value |
| --- | --- |
| verdict accuracy vs label | 0.00 |
| TP retention, raw contradicts | 0.00 |
| TP retention, high-confidence | 0.00 |
| **antonym FP rate, raw contradicts** | **0.00** |
| **antonym FP rate, high-confidence** | **0.00** |
| **benefit-limitation FP rate, raw contradicts** | **0.00** |
| **benefit-limitation FP rate, high-confidence** | **0.00** |
| **compatible-statement FP rate, raw contradicts** | **0.00** |
| **compatible-statement FP rate, high-confidence** | **0.00** |
| evaluative-contradiction retention, raw contradicts | 0.00 |
| **merged-content compatible FP (wrong verdicts), n of TOTAL** | **0 of 120** |
| merged-content contradiction missed, n of TOTAL | 0 of 30 |
| evaluative-contradiction retention, high-confidence | 0.00 |
| mean stability (modal share) | 0.00 |
| mean run latency | 32.7s |
| mean confidence, CORRECT verdicts | 0.00 |
| mean confidence, WRONG verdicts | 0.00 |

## Merged-content cases (wrong verdicts, n of TOTAL, per probe)

- `merged-complementary`: 0 of 60
- `merged-contradiction`: 0 of 30
- `merged-identical`: 0 of 30
- `merged-long`: 0 of 30

## Per pair

| pair | probe | expected | modal | acc | stab | confidences |
| --- | --- | --- | --- | --- | --- | --- |
