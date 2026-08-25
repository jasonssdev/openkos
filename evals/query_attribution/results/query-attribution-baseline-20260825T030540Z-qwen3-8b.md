# query attribution by language x length — arm `baseline` (#871)

_Generated: 20260825T030540Z_ · model `qwen3:8b` · **3 runs** · 60 answers · 0 failures.

Generation ceiling `8192` · context window `12288`.

All questions are grounded; `sufficiency_check` off (see module docstring). `compliance` = share of answers whose attribution is `reported`.

| cell | n | reported | absent | unparsed | compliance | chars min/med/max | cited mean |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `es-short` | 15 | 15 | 0 | 0 | 1.00 | 90/245/413 | 1.8 |
| `es-long` | 15 | 13 | 2 | 0 | 0.87 | 618/1165/2500 | 4.0 |
| `en-short` | 15 | 7 | 8 | 0 | 0.47 | 81/254/605 | 3.6 |
| `en-long` | 15 | 12 | 3 | 0 | 0.80 | 667/1310/2066 | 4.1 |

## Non-reported answers

| run | cell | attribution | chars | question |
| --- | --- | --- | --- | --- |
| 1 | `es-long` | absent | 2026 | haz un resumen completo y estructurado, con secciones, de to |
| 2 | `es-long` | absent | 2347 | haz un resumen completo y estructurado, con secciones, de to |
| 0 | `en-short` | absent | 203 | what happens if I ingest the same file twice? |
| 0 | `en-short` | absent | 81 | who was assigned as owner of the migration? |
| 0 | `en-short` | absent | 240 | is the bundle or the index the source of truth? |
| 0 | `en-long` | absent | 811 | list and elaborate every architecture principle of the proje |
| 1 | `en-short` | absent | 81 | who was assigned as owner of the migration? |
| 1 | `en-short` | absent | 240 | is the bundle or the index the source of truth? |
| 1 | `en-short` | absent | 473 | why are verbatim citations required? |
| 1 | `en-long` | absent | 1317 | list and elaborate every architecture principle of the proje |
| 2 | `en-short` | absent | 81 | who was assigned as owner of the migration? |
| 2 | `en-short` | absent | 242 | is the bundle or the index the source of truth? |
| 2 | `en-long` | absent | 889 | list and elaborate every architecture principle of the proje |
