# adjudication eval — arm `baseline` (#796)

_Generated: 20260828T123442Z_ · model `qwen3:8b` · **15 runs** over 22 labelled pairs.

Generation ceiling `8192` · context window `12288`.

Labels are CONSTRUCTED, not adjudicated — see `adjudication_fixtures.py`.
Rationales are not printed here; every one is stored verbatim in the sibling `runs-*.json` (#807).

| metric | value |
| --- | --- |
| **recurrence precision (judged `different`)** | **1.00** |
| **event-same retention (judged `same`)** | **1.00** |
| **asym-recurrence precision (judged `different`)** | **0.64** |
| **asym-same retention (judged `same`)** | **0.97** |
| person-same retention (judged `same`) | 1.00 |
| alias-same retention (judged `same`) | 1.00 |
| part-whole (judged `different`) | 1.00 |
| **aspect-of (judged `different`)** | **1.00** |
| **transitivity pairs (judged `different`)** | **1.00** |
| **transitivity violation rate (2-SAME-1-DIFFERENT triangles)** | **0.00** |
| triangles scored | 60 of 60 |
| runs with ≥1 triangle violation | 0 of 15 |
| verdict accuracy vs label | 0.95 |
| mean stability (modal share) | 0.98 |
| mean run latency | 57.8s |
| mean confidence, CORRECT verdicts | 0.96 |
| mean confidence, WRONG verdicts | 0.95 |

## Per probe

| probe | expected | n | same | different | uncertain | missing |
| --- | --- | --- | --- | --- | --- | --- |
| recurrence | `different` | 45 | 0.00 | 1.00 | 0.00 | 0.00 |
| event-same | `same` | 30 | 1.00 | 0.00 | 0.00 | 0.00 |
| asym-recurrence | `different` | 45 | 0.36 | 0.64 | 0.00 | 0.00 |
| asym-same | `same` | 30 | 0.97 | 0.03 | 0.00 | 0.00 |
| person-same | `same` | 15 | 1.00 | 0.00 | 0.00 | 0.00 |
| alias-same | `same` | 15 | 1.00 | 0.00 | 0.00 | 0.00 |
| part-whole | `different` | 15 | 0.00 | 1.00 | 0.00 | 0.00 |
| aspect-of | `different` | 45 | 0.00 | 1.00 | 0.00 | 0.00 |
| transitivity | `different` | 90 | 0.00 | 1.00 | 0.00 | 0.00 |

## Per pair

| pair | probe | expected | modal | acc | stab | confidences |
| --- | --- | --- | --- | --- | --- | --- |
| events/comite-evaluacion-coordinacion <-> events/comite-evaluacion-coordinacion-2 | recurrence | `different` | `different` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| events/weekly-design-review <-> events/weekly-design-review-2 | recurrence | `different` | `different` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 0.95, 1.00, 1.00, 1.00, 0.95, 1.00, 1.00, 1.00, 1.00, 1.00, 0.95, 1.00 |
| events/sprint-retrospective <-> events/sprint-retrospective-2 | recurrence | `different` | `different` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 1.00, 0.95, 0.95, 1.00, 0.95, 0.95, 0.95, 0.95, 1.00, 0.95, 0.95 |
| events/kickoff-plataforma-orion <-> events/kickoff-plataforma-orion-2 | event-same | `same` | `same` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| events/q3-budget-signoff <-> events/q3-budget-signoff-2 | event-same | `same` | `same` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| events/grupo-calidad-datos <-> events/grupo-calidad-datos-2 | asym-recurrence | `different` | `same` | 0.13 | 0.87 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| events/platform-reliability-sync <-> events/platform-reliability-sync-2 | asym-recurrence | `different` | `different` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| events/onboarding-working-group <-> events/onboarding-working-group-2 | asym-recurrence | `different` | `different` | 0.80 | 0.80 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| events/sync-arquitectura <-> events/sync-arquitectura-2 | asym-same | `same` | `same` | 0.93 | 0.93 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| events/vendor-selection-review <-> events/vendor-selection-review-2 | asym-same | `same` | `same` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| people/marta-ruiz <-> people/marta-ruiz-2 | person-same | `same` | `same` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| concepts/model-context-protocol <-> concepts/protocolo-model-context | alias-same | `same` | `same` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| concepts/ingest-pipeline <-> concepts/ingest-pipeline-scheduler | part-whole | `different` | `different` | 1.00 | 1.00 | 1.00, 0.95, 1.00, 0.95, 1.00, 1.00, 0.95, 1.00, 1.00, 1.00, 0.95, 1.00, 0.95, 1.00, 1.00 |
| concepts/atlas-data-platform <-> concepts/components-of-the-atlas-data-platform | aspect-of | `different` | `different` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/meridian-archive-service <-> concepts/storage-in-the-meridian-archive-service | aspect-of | `different` | `different` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/quorum-review-workflow <-> concepts/governance-of-the-quorum-review-workflow | aspect-of | `different` | `different` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
| projects/evaluacion-de-decisiones <-> events/reunion-de-evaluacion-de-decisiones-1 | transitivity | `different` | `different` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| projects/evaluacion-de-decisiones <-> events/reunion-de-evaluacion-de-decisiones-2 | transitivity | `different` | `different` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| projects/evaluacion-de-decisiones <-> events/reunion-de-evaluacion-de-decisiones-3 | transitivity | `different` | `different` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| events/reunion-de-evaluacion-de-decisiones-1 <-> events/reunion-de-evaluacion-de-decisiones-2 | transitivity | `different` | `different` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| events/reunion-de-evaluacion-de-decisiones-1 <-> events/reunion-de-evaluacion-de-decisiones-3 | transitivity | `different` | `different` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| events/reunion-de-evaluacion-de-decisiones-2 <-> events/reunion-de-evaluacion-de-decisiones-3 | transitivity | `different` | `different` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
