# query attribution by language x length — arm `treatment` (#871)

_Generated: 20260825T032216Z_ · model `qwen3:8b` · **3 runs** · 60 answers · 0 failures.

Generation ceiling `8192` · context window `12288`.

All questions are grounded; `sufficiency_check` off (see module docstring). `compliance` = share of answers whose attribution is `reported`.

| cell | n | reported | absent | unparsed | compliance | chars min/med/max | cited mean |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `es-short` | 15 | 15 | 0 | 0 | 1.00 | 90/241/332 | 1.8 |
| `es-long` | 15 | 15 | 0 | 0 | 1.00 | 647/1461/2902 | 4.3 |
| `en-short` | 15 | 12 | 3 | 0 | 0.80 | 73/355/619 | 2.8 |
| `en-long` | 15 | 13 | 2 | 0 | 0.87 | 617/1265/1664 | 3.9 |

## Non-reported answers

| run | cell | attribution | chars | question |
| --- | --- | --- | --- | --- |
| 0 | `en-long` | absent | 1048 | explain in detail the project's privacy model: sensitivity,  |
| 1 | `en-short` | absent | 316 | what happens if I ingest the same file twice? |
| 1 | `en-short` | absent | 514 | why are verbatim citations required? |
| 1 | `en-long` | absent | 818 | list and elaborate every architecture principle of the proje |
| 2 | `en-short` | absent | 324 | what happens if I ingest the same file twice? |
