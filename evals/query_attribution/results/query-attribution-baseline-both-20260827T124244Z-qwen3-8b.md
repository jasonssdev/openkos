# query attribution by context x language x length — arm `baseline` (#871, #887)

_Generated: 20260827T124244Z_ · model `qwen3:8b` · **3 runs** · 120 answers · 0 failures.

Generation ceiling `8192` · context window `12288`.


All questions are grounded; `sufficiency_check` off (see module docstring). `compliance` = share of answers whose attribution is `reported`. `ptok` is the MEASURED median `prompt_eval_count` for the cell — the regime label the rate belongs to. `exc`/`om` are the mean context blocks sent as an excerpt / shown not at all (#882). `nomatch` must be 0 for a cell's compliance to mean anything.

| cell | n | reported | absent | unparsed | compliance | ptok | exc | om | nomatch | chars min/med/max | cited mean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `small-es-short` | 15 | 15 | 0 | 0 | 1.00 | 879 | 0.0 | 0.0 | 0 | 90/318/422 | 2.3 |
| `small-es-long` | 15 | 15 | 0 | 0 | 1.00 | 903 | 0.0 | 0.0 | 0 | 586/1084/2144 | 3.9 |
| `small-en-short` | 15 | 10 | 5 | 0 | 0.67 | 685 | 0.0 | 0.0 | 0 | 73/403/585 | 3.2 |
| `small-en-long` | 15 | 14 | 1 | 0 | 0.93 | 705 | 0.0 | 0.0 | 0 | 572/1118/1578 | 3.9 |
| `large-es-short` | 15 | 15 | 0 | 0 | 1.00 | 2542 | 2.6 | 0.0 | 0 | 64/335/546 | 2.1 |
| `large-es-long` | 15 | 15 | 0 | 0 | 1.00 | 2572 | 2.0 | 0.0 | 0 | 790/1538/2425 | 3.9 |
| `large-en-short` | 15 | 13 | 2 | 0 | 0.87 | 2305 | 2.4 | 0.0 | 0 | 60/310/710 | 2.7 |
| `large-en-long` | 15 | 15 | 0 | 0 | 1.00 | 2325 | 2.4 | 0.0 | 0 | 1050/1847/2592 | 4.1 |

## Rung totals

| context | n | compliance | ptok median | sent chars median |
| --- | --- | --- | --- | --- |
| `small` | 60 | 0.90 | 777 | 3184 |
| `large` | 60 | 0.97 | 2348 | 9704 |

## Non-reported answers

| run | cell | attribution | ptok | chars | question |
| --- | --- | --- | --- | --- | --- |
| 0 | `small-en-short` | absent | 664 | 434 | what was decided about the decision history? |
| 0 | `small-en-short` | absent | 885 | 303 | what happens if I ingest the same file twice? |
| 0 | `small-en-short` | absent | 685 | 585 | why are verbatim citations required? |
| 2 | `small-en-short` | absent | 664 | 439 | what was decided about the decision history? |
| 2 | `small-en-short` | absent | 885 | 357 | what happens if I ingest the same file twice? |
| 2 | `small-en-long` | absent | 629 | 1005 | explain in detail the project's privacy model: sensitivi |
| 0 | `large-en-short` | absent | 2092 | 310 | is the bundle or the index the source of truth? |
| 0 | `large-en-short` | absent | 2322 | 418 | why are verbatim citations required? |
