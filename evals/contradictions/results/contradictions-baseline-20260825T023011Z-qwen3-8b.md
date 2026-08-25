# contradiction-judge eval — arm `baseline` (#558)

_Generated: 20260825T023011Z_ · model `qwen3:8b` · **15 runs** over 18 labelled pairs.

Generation ceiling `8192` · context window `12288`.

Labels are CONSTRUCTED, not adjudicated — see `contradiction_fixtures.py`.

| metric | value |
| --- | --- |
| verdict accuracy vs label | 0.86 |
| TP retention, raw contradicts | 1.00 |
| TP retention, high-confidence | 1.00 |
| **antonym FP rate, raw contradicts** | **0.32** |
| **antonym FP rate, high-confidence** | **0.32** |
| **benefit-limitation FP rate, raw contradicts** | **0.15** |
| **benefit-limitation FP rate, high-confidence** | **0.15** |
| evaluative-contradiction retention, raw contradicts | 1.00 |
| evaluative-contradiction retention, high-confidence | 1.00 |
| mean stability (modal share) | 0.95 |
| mean run latency | 48.5s |
| mean confidence, CORRECT verdicts | 0.97 |
| mean confidence, WRONG verdicts | 0.98 |

## Per pair

| pair | probe | expected | modal | acc | stab | confidences |
| --- | --- | --- | --- | --- | --- | --- |
| concepts/okp-standard-history <-> concepts/okp-standard-overview | factual-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 1.00, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| concepts/free-tier-limits <-> concepts/free-tier-billing | factual-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| concepts/legacy-exporter-status <-> concepts/exporter-migration | factual-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| events/march-outage-cause <-> events/march-outage-review | factual-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| concepts/personalized-recommendation <-> concepts/non-personalized-recommendation | antonym | `consistent` | `contradicts` | 0.33 | 0.67 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/synchronous-replication <-> concepts/asynchronous-replication | antonym | `consistent` | `consistent` | 0.93 | 0.93 | 1.00, 1.00, 1.00, 0.90, 0.95, 0.95, 1.00, 0.95, 0.95, 0.95, 0.95, 0.95, 1.00, 1.00, 0.95 |
| concepts/allowlist-filtering <-> concepts/denylist-filtering | antonym | `consistent` | `contradicts` | 0.13 | 0.87 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/optimistic-locking <-> concepts/pessimistic-locking | antonym | `consistent` | `consistent` | 1.00 | 1.00 | 0.90, 0.90, 1.00, 1.00, 1.00, 1.00, 0.95, 0.90, 1.00, 0.90, 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/supervised-learning <-> concepts/unsupervised-learning | antonym | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 0.90, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/retry-budget <-> concepts/request-scheduler | plain-consistent | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/bundle-format <-> concepts/concept-document | plain-consistent | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/client-default-timeout <-> concepts/client-timeout-behavior | definitional-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/generacion-aumentada-por-recuperacion <-> concepts/trazabilidad-en-sistemas-rag | benefit-limitation | `consistent` | `contradicts` | 0.07 | 0.60 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| concepts/caching-layer <-> concepts/cache-invalidation | benefit-limitation | `consistent` | `consistent` | 1.00 | 1.00 | 0.90, 0.95, 0.90, 0.90, 0.95, 0.90, 0.95, 0.90, 0.90, 0.90, 0.95, 0.90, 0.90, 0.95, 0.90 |
| concepts/microservices-autonomy <-> concepts/microservices-operational-load | benefit-limitation | `consistent` | `consistent` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95 |
| concepts/secondary-indexes-reads <-> concepts/index-write-amplification | benefit-limitation | `consistent` | `consistent` | 1.00 | 1.00 | 0.95, 0.95, 1.00, 0.90, 1.00, 0.95, 0.95, 0.95, 1.00, 0.90, 0.95, 0.95, 0.90, 0.95, 0.95 |
| concepts/compression-benchmark-result <-> concepts/compression-latency-review | evaluative-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 0.95, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/event-bus-rollout-outcome <-> concepts/event-bus-rollout-retrospective | evaluative-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 0.95, 1.00, 1.00, 1.00, 1.00, 1.00 |
