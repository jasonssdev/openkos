# contradiction-judge eval — arm `baseline` (#558)

_Generated: 20260812T223612Z_ · model `qwen3:8b` · **5 runs** over 12 labelled pairs.

Labels are CONSTRUCTED, not adjudicated — see `contradiction_fixtures.py`.

| metric | value |
| --- | --- |
| verdict accuracy vs label | 0.88 |
| TP retention, raw contradicts | 1.00 |
| TP retention, high-confidence | 1.00 |
| **antonym FP rate, raw contradicts** | **0.28** |
| **antonym FP rate, high-confidence** | **0.28** |
| mean stability (modal share) | 0.95 |
| mean run latency | 30.4s |
| mean confidence, CORRECT verdicts | 0.97 |
| mean confidence, WRONG verdicts | 0.99 |

## Per pair

| pair | probe | expected | modal | acc | stab | confidences |
| --- | --- | --- | --- | --- | --- | --- |
| concepts/okp-standard-history <-> concepts/okp-standard-overview | factual-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95 |
| concepts/free-tier-limits <-> concepts/free-tier-billing | factual-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95 |
| concepts/legacy-exporter-status <-> concepts/exporter-migration | factual-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95 |
| events/march-outage-cause <-> events/march-outage-review | factual-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95 |
| concepts/personalized-recommendation <-> concepts/non-personalized-recommendation | antonym | `consistent` | `contradicts` | 0.40 | 0.60 | 1.00, 1.00, 1.00, 0.95, 1.00 |
| concepts/synchronous-replication <-> concepts/asynchronous-replication | antonym | `consistent` | `consistent` | 1.00 | 1.00 | 0.95, 0.95, 1.00, 0.95, 0.95 |
| concepts/allowlist-filtering <-> concepts/denylist-filtering | antonym | `consistent` | `contradicts` | 0.20 | 0.80 | 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/optimistic-locking <-> concepts/pessimistic-locking | antonym | `consistent` | `consistent` | 1.00 | 1.00 | 0.90, 0.90, 0.90, 1.00, 1.00 |
| concepts/supervised-learning <-> concepts/unsupervised-learning | antonym | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/retry-budget <-> concepts/request-scheduler | plain-consistent | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/bundle-format <-> concepts/concept-document | plain-consistent | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/client-default-timeout <-> concepts/client-timeout-behavior | definitional-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00 |
