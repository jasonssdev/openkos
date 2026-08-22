# adjudication eval — arm `baseline` — ABLATED: nowithdrawal (#796)

_Generated: 20260822T165054Z_ · model `qwen3:8b` · **15 runs** over 8 labelled pairs.

Generation ceiling `8192` · context window `12288`.

Labels are CONSTRUCTED, not adjudicated — see `adjudication_fixtures.py`.
Rationales are not printed here; every one is stored verbatim in the sibling `runs-*.json` (#807).

| metric | value |
| --- | --- |
| **recurrence precision (judged `different`)** | **1.00** |
| **event-same retention (judged `same`)** | **1.00** |
| person-same retention (judged `same`) | 1.00 |
| alias-same retention (judged `same`) | 1.00 |
| part-whole (judged `different`) | 1.00 |
| verdict accuracy vs label | 1.00 |
| mean stability (modal share) | 1.00 |
| mean run latency | 18.6s |
| mean confidence, CORRECT verdicts | 0.96 |
| mean confidence, WRONG verdicts | 0.00 |

## Per probe

| probe | expected | n | same | different | uncertain | missing |
| --- | --- | --- | --- | --- | --- | --- |
| recurrence | `different` | 45 | 0.00 | 1.00 | 0.00 | 0.00 |
| event-same | `same` | 30 | 1.00 | 0.00 | 0.00 | 0.00 |
| person-same | `same` | 15 | 1.00 | 0.00 | 0.00 | 0.00 |
| alias-same | `same` | 15 | 1.00 | 0.00 | 0.00 | 0.00 |
| part-whole | `different` | 15 | 0.00 | 1.00 | 0.00 | 0.00 |

## Per pair

| pair | probe | expected | modal | acc | stab | confidences |
| --- | --- | --- | --- | --- | --- | --- |
| events/comite-evaluacion-coordinacion <-> events/comite-evaluacion-coordinacion-2 | recurrence | `different` | `different` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| events/weekly-design-review <-> events/weekly-design-review-2 | recurrence | `different` | `different` | 1.00 | 1.00 | 1.00, 1.00, 0.95, 1.00, 0.95, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 0.95, 1.00 |
| events/sprint-retrospective <-> events/sprint-retrospective-2 | recurrence | `different` | `different` | 1.00 | 1.00 | 0.95, 1.00, 0.95, 0.95, 0.95, 0.95, 1.00, 0.95, 1.00, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| events/kickoff-plataforma-orion <-> events/kickoff-plataforma-orion-2 | event-same | `same` | `same` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| events/q3-budget-signoff <-> events/q3-budget-signoff-2 | event-same | `same` | `same` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| people/marta-ruiz <-> people/marta-ruiz-2 | person-same | `same` | `same` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| concepts/model-context-protocol <-> concepts/protocolo-model-context | alias-same | `same` | `same` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| concepts/ingest-pipeline <-> concepts/ingest-pipeline-scheduler | part-whole | `different` | `different` | 1.00 | 1.00 | 1.00, 0.95, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
