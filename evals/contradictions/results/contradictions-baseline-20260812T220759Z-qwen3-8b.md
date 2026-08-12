# contradiction-judge eval — arm `baseline` (#558)

_Generated: 20260812T220759Z_ · model `qwen3:8b` · **5 runs** over 12 labelled pairs.

Labels are CONSTRUCTED, not adjudicated — see `fixtures.py`.

| metric | value |
| --- | --- |
| verdict accuracy vs label | 0.83 |
| TP retention, raw contradicts | 1.00 |
| TP retention, high-confidence | 1.00 |
| **antonym FP rate, raw contradicts** | **0.40** |
| **antonym FP rate, high-confidence** | **0.40** |
| mean stability (modal share) | 1.00 |
| mean run latency | 27.3s |
| mean confidence, CORRECT verdicts | 1.00 |
| mean confidence, WRONG verdicts | 1.00 |

## Per pair

| pair | probe | expected | modal | acc | stab | confidences |
| --- | --- | --- | --- | --- | --- | --- |
| concepts/okp-standard-history <-> concepts/okp-standard-overview | factual-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/free-tier-limits <-> concepts/free-tier-billing | factual-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/legacy-exporter-status <-> concepts/exporter-migration | factual-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00 |
| events/march-outage-cause <-> events/march-outage-review | factual-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/personalized-recommendation <-> concepts/non-personalized-recommendation | antonym | `consistent` | `contradicts` | 0.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/synchronous-replication <-> concepts/asynchronous-replication | antonym | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/allowlist-filtering <-> concepts/denylist-filtering | antonym | `consistent` | `contradicts` | 0.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/optimistic-locking <-> concepts/pessimistic-locking | antonym | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/supervised-learning <-> concepts/unsupervised-learning | antonym | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/retry-budget <-> concepts/request-scheduler | plain-consistent | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/bundle-format <-> concepts/concept-document | plain-consistent | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/client-default-timeout <-> concepts/client-timeout-behavior | definitional-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00 |
