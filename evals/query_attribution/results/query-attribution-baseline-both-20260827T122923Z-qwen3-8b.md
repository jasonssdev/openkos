# query attribution by context x language x length — arm `baseline` (#871, #887)

_Generated: 20260827T122923Z_ · model `qwen3:8b` · **3 runs** · 120 answers · 0 failures.

Generation ceiling `8192` · context window `12288`.

All questions are grounded; `sufficiency_check` off (see module docstring). `compliance` = share of answers whose attribution is `reported`. `ptok` is the MEASURED median `prompt_eval_count` for the cell — the regime label the rate belongs to. `exc`/`om` are the mean context blocks sent as an excerpt / shown not at all (#882). `nomatch` must be 0 for a cell's compliance to mean anything.

| cell | n | reported | absent | unparsed | compliance | ptok | exc | om | nomatch | chars min/med/max | cited mean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `small-es-short` | 15 | 15 | 0 | 0 | 1.00 | 879 | 0.0 | 0.0 | 0 | 90/241/428 | 2.2 |
| `small-es-long` | 15 | 13 | 2 | 0 | 0.87 | 903 | 0.0 | 0.0 | 0 | 605/1204/1867 | 4.1 |
| `small-en-short` | 15 | 14 | 1 | 0 | 0.93 | 685 | 0.0 | 0.0 | 0 | 73/387/546 | 2.7 |
| `small-en-long` | 15 | 10 | 5 | 0 | 0.67 | 705 | 0.0 | 0.0 | 0 | 532/1104/2015 | 4.1 |
| `large-es-short` | 15 | 15 | 0 | 0 | 1.00 | 2542 | 2.6 | 0.0 | 0 | 64/336/548 | 2.2 |
| `large-es-long` | 15 | 14 | 1 | 0 | 0.93 | 2572 | 2.0 | 0.0 | 0 | 844/1678/2334 | 4.2 |
| `large-en-short` | 15 | 13 | 2 | 0 | 0.87 | 2305 | 2.4 | 0.0 | 0 | 60/284/597 | 2.5 |
| `large-en-long` | 15 | 15 | 0 | 0 | 1.00 | 2325 | 2.4 | 0.0 | 0 | 1033/1910/2851 | 3.9 |

## Rung totals

| context | n | compliance | ptok median | sent chars median |
| --- | --- | --- | --- | --- |
| `small` | 60 | 0.87 | 777 | 3184 |
| `large` | 60 | 0.95 | 2348 | 9704 |

## Non-reported answers

| run | cell | attribution | ptok | chars | question |
| --- | --- | --- | --- | --- | --- |
| 1 | `small-es-long` | absent | 822 | 1266 | haz un resumen completo y estructurado, con secciones, d |
| 2 | `small-es-long` | absent | 822 | 1393 | haz un resumen completo y estructurado, con secciones, d |
| 0 | `small-en-short` | absent | 664 | 434 | what was decided about the decision history? |
| 0 | `small-en-long` | absent | 629 | 867 | explain in detail the project's privacy model: sensitivi |
| 0 | `small-en-long` | absent | 656 | 610 | describe the full flow from ingestion to publishing deri |
| 0 | `small-en-long` | absent | 705 | 1203 | write a detailed report on the knowledge compiler's trac |
| 0 | `small-en-long` | absent | 758 | 2015 | list and elaborate every architecture principle of the p |
| 2 | `small-en-long` | absent | 758 | 1609 | list and elaborate every architecture principle of the p |
| 1 | `large-es-long` | absent | 2353 | 2124 | haz un resumen completo y estructurado, con secciones, d |
| 1 | `large-en-short` | absent | 2305 | 481 | what was decided about the decision history? |
| 2 | `large-en-short` | absent | 2305 | 323 | what was decided about the decision history? |
