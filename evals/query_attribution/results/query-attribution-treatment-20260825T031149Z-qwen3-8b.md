# query attribution by language x length — arm `treatment` (#871)

_Generated: 20260825T031149Z_ · model `qwen3:8b` · **3 runs** · 60 answers · 0 failures.

Generation ceiling `8192` · context window `12288`.

All questions are grounded; `sufficiency_check` off (see module docstring). `compliance` = share of answers whose attribution is `reported`.

| cell | n | reported | absent | unparsed | compliance | chars min/med/max | cited mean |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `es-short` | 15 | 15 | 0 | 0 | 1.00 | 90/234/412 | 1.8 |
| `es-long` | 15 | 15 | 0 | 0 | 1.00 | 584/1456/2337 | 4.1 |
| `en-short` | 15 | 11 | 4 | 0 | 0.73 | 73/343/526 | 2.9 |
| `en-long` | 15 | 12 | 3 | 0 | 0.80 | 660/1324/1581 | 4.1 |

## Non-reported answers

| run | cell | attribution | chars | question |
| --- | --- | --- | --- | --- |
| 0 | `en-short` | absent | 452 | why are verbatim citations required? |
| 0 | `en-long` | absent | 802 | list and elaborate every architecture principle of the proje |
| 1 | `en-short` | absent | 448 | why are verbatim citations required? |
| 1 | `en-long` | absent | 718 | list and elaborate every architecture principle of the proje |
| 2 | `en-short` | absent | 316 | what happens if I ingest the same file twice? |
| 2 | `en-short` | absent | 526 | why are verbatim citations required? |
| 2 | `en-long` | absent | 814 | list and elaborate every architecture principle of the proje |
