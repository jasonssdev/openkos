# contradiction-judge eval — arm `baseline` (#558)

_Generated: 20261002T160234Z_ · model `qwen3:8b` · **15 runs** over 22 labelled pairs.

Generation ceiling `8192` · context window `12288`.

Labels are CONSTRUCTED, not adjudicated — see `contradiction_fixtures.py`.

| metric | value |
| --- | --- |
| verdict accuracy vs label | 1.00 |
| TP retention, raw contradicts | 1.00 |
| TP retention, high-confidence | 1.00 |
| **antonym FP rate, raw contradicts** | **0.00** |
| **antonym FP rate, high-confidence** | **0.00** |
| **benefit-limitation FP rate, raw contradicts** | **0.00** |
| **benefit-limitation FP rate, high-confidence** | **0.00** |
| **compatible-statement FP rate, raw contradicts** | **0.00** |
| **compatible-statement FP rate, high-confidence** | **0.00** |
| evaluative-contradiction retention, raw contradicts | 1.00 |
| **merged-content compatible FP (wrong verdicts), n of TOTAL** | **44 of 270** |
| **#1223 field shapes (scope-guidance + narrower-use), wrong, n of TOTAL** | **44 of 150** |
| merged-content contradiction missed, n of TOTAL | 0 of 60 |
| evaluative-contradiction retention, high-confidence | 1.00 |
| mean stability (modal share) | 1.00 |
| mean run latency | 121.3s |
| mean confidence, CORRECT verdicts | 0.97 |
| mean confidence, WRONG verdicts | 0.00 |

## Merged-content cases (wrong verdicts, n of TOTAL, per probe)

- `merged-complementary`: 0 of 60
- `merged-contradiction`: 0 of 60
- `merged-identical`: 0 of 30
- `merged-long`: 0 of 30
- `merged-narrower-use`: 29 of 75
- `merged-scope-guidance`: 15 of 75

## Merged-content cases, wrong verdicts per case (of 15 runs)

- `people/lucia-paredes` (merged-complementary, expected `consistent`): 0 of 15
- `people/tomas-herrera` (merged-complementary, expected `consistent`): 0 of 15
- `concepts/servicio-notificaciones` (merged-complementary, expected `consistent`): 0 of 15
- `people/priya-nair` (merged-complementary, expected `consistent`): 0 of 15
- `concepts/recuperacion-documentos` (merged-identical, expected `consistent`): 0 of 15
- `concepts/trabajo-nocturno` (merged-identical, expected `consistent`): 0 of 15
- `concepts/plataforma-datos` (merged-long, expected `consistent`): 0 of 15
- `concepts/plataforma-analitica` (merged-long, expected `consistent`): 0 of 15
- `concepts/proyecto-aurora` (merged-contradiction, expected `contradicts`): 0 of 15
- `concepts/compresion-lecturas` (merged-contradiction, expected `contradicts`): 0 of 15
- `concepts/claude-md` (merged-scope-guidance, expected `consistent`): 15 of 15
- `concepts/makefile` (merged-scope-guidance, expected `consistent`): 0 of 15
- `concepts/archivo-env` (merged-scope-guidance, expected `consistent`): 0 of 15
- `concepts/readme` (merged-scope-guidance, expected `consistent`): 0 of 15
- `concepts/changelog` (merged-scope-guidance, expected `consistent`): 0 of 15
- `entities/claude-md` (merged-narrower-use, expected `consistent`): 15 of 15
- `concepts/cache` (merged-narrower-use, expected `consistent`): 0 of 15
- `concepts/bitacora` (merged-narrower-use, expected `consistent`): 14 of 15
- `concepts/cola-trabajo` (merged-narrower-use, expected `consistent`): 0 of 15
- `concepts/tablero-kanban` (merged-narrower-use, expected `consistent`): 0 of 15
- `concepts/makefile-logic` (merged-contradiction, expected `contradicts`): 0 of 15
- `concepts/cache-lectura` (merged-contradiction, expected `contradicts`): 0 of 15

## Per pair

| pair | probe | expected | modal | acc | stab | confidences |
| --- | --- | --- | --- | --- | --- | --- |
| concepts/okp-standard-history <-> concepts/okp-standard-overview | factual-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 1.00, 0.95, 0.95, 0.95, 0.95 |
| concepts/free-tier-limits <-> concepts/free-tier-billing | factual-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| concepts/legacy-exporter-status <-> concepts/exporter-migration | factual-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| events/march-outage-cause <-> events/march-outage-review | factual-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| concepts/personalized-recommendation <-> concepts/non-personalized-recommendation | antonym | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 0.95, 0.90, 0.90, 0.90, 1.00, 0.90, 0.90, 1.00, 0.95, 1.00, 1.00, 0.90, 0.95, 0.90 |
| concepts/synchronous-replication <-> concepts/asynchronous-replication | antonym | `consistent` | `consistent` | 1.00 | 1.00 | 0.95, 1.00, 0.95, 0.95, 0.95, 0.95, 1.00, 1.00, 0.95, 0.95, 0.95, 1.00, 0.95, 1.00, 0.95 |
| concepts/allowlist-filtering <-> concepts/denylist-filtering | antonym | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/optimistic-locking <-> concepts/pessimistic-locking | antonym | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 0.90, 0.90, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/supervised-learning <-> concepts/unsupervised-learning | antonym | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/retry-budget <-> concepts/request-scheduler | plain-consistent | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/bundle-format <-> concepts/concept-document | plain-consistent | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/client-default-timeout <-> concepts/client-timeout-behavior | definitional-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/generacion-aumentada-por-recuperacion <-> concepts/trazabilidad-en-sistemas-rag | benefit-limitation | `consistent` | `consistent` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| concepts/caching-layer <-> concepts/cache-invalidation | benefit-limitation | `consistent` | `consistent` | 1.00 | 1.00 | 0.95, 0.90, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.90, 1.00, 0.90, 0.95, 0.95, 0.95, 0.95 |
| concepts/microservices-autonomy <-> concepts/microservices-operational-load | benefit-limitation | `consistent` | `consistent` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| concepts/secondary-indexes-reads <-> concepts/index-write-amplification | benefit-limitation | `consistent` | `consistent` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.90, 0.95, 0.90, 0.90, 0.90, 0.95, 0.95, 0.95, 0.95, 1.00, 0.90 |
| concepts/compression-benchmark-result <-> concepts/compression-latency-review | evaluative-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/event-bus-rollout-outcome <-> concepts/event-bus-rollout-retrospective | evaluative-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 0.95, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
| people/ana-ruiz-participant <-> people/ana-ruiz-tester | complementary-description | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 1.00, 0.95, 1.00, 0.95, 0.95, 1.00, 1.00, 0.95, 1.00, 1.00, 1.00, 0.95, 1.00, 0.95 |
| people/marco-silva-role <-> people/marco-silva-talk | complementary-description | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/rag-limitaciones <-> concepts/trazabilidad-sistemas-informacion | identical-statement | `consistent` | `consistent` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 1.00, 1.00, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| concepts/batch-job-idempotency <-> concepts/nightly-export-caveats | identical-statement | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
