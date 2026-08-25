# query attribution by language x length — arm `baseline` (#871)

_Generated: 20260825T031731Z_ · model `qwen3:8b` · **3 runs** · 60 answers · 0 failures.

Generation ceiling `8192` · context window `12288`.

All questions are grounded; `sufficiency_check` off (see module docstring). `compliance` = share of answers whose attribution is `reported`.

| cell | n | reported | absent | unparsed | compliance | chars min/med/max | cited mean |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `es-short` | 15 | 15 | 0 | 0 | 1.00 | 90/264/406 | 1.9 |
| `es-long` | 15 | 12 | 3 | 0 | 0.80 | 572/1279/2394 | 4.1 |
| `en-short` | 15 | 12 | 3 | 0 | 0.80 | 73/276/542 | 2.4 |
| `en-long` | 15 | 11 | 4 | 0 | 0.73 | 635/1005/1772 | 3.7 |

## Non-reported answers

| run | cell | attribution | chars | question |
| --- | --- | --- | --- | --- |
| 0 | `es-long` | absent | 2295 | haz un resumen completo y estructurado, con secciones, de to |
| 1 | `es-long` | absent | 1717 | haz un resumen completo y estructurado, con secciones, de to |
| 2 | `es-long` | absent | 2394 | haz un resumen completo y estructurado, con secciones, de to |
| 0 | `en-short` | absent | 240 | is the bundle or the index the source of truth? |
| 0 | `en-long` | absent | 805 | describe the full flow from ingestion to publishing derived  |
| 0 | `en-long` | absent | 753 | list and elaborate every architecture principle of the proje |
| 1 | `en-long` | absent | 789 | list and elaborate every architecture principle of the proje |
| 2 | `en-short` | absent | 339 | what was decided about the decision history? |
| 2 | `en-short` | absent | 240 | is the bundle or the index the source of truth? |
| 2 | `en-long` | absent | 858 | list and elaborate every architecture principle of the proje |
