# contradiction-judge eval — arm `baseline` (#558)

_Generated: 20260816T085255Z_ · model `qwen2.5:3b` · **15 runs** over 12 labelled pairs.

Labels are CONSTRUCTED, not adjudicated — see `contradiction_fixtures.py`.

| metric | value |
| --- | --- |
| verdict accuracy vs label | 0.86 |
| TP retention, raw contradicts | 1.00 |
| TP retention, high-confidence | 1.00 |
| **antonym FP rate, raw contradicts** | **0.33** |
| **antonym FP rate, high-confidence** | **0.33** |
| mean stability (modal share) | 0.96 |
| mean run latency | 13.2s |
| mean confidence, CORRECT verdicts | 0.98 |
| mean confidence, WRONG verdicts | 0.96 |

## Per pair

| pair | probe | expected | modal | acc | stab | confidences |
| --- | --- | --- | --- | --- | --- | --- |
| concepts/okp-standard-history <-> concepts/okp-standard-overview | factual-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 0.95, 0.95, 1.00, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 1.00, 0.95 |
| concepts/free-tier-limits <-> concepts/free-tier-billing | factual-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 0.90, 0.95, 0.90, 1.00, 0.90, 0.90, 1.00, 1.00, 0.95, 1.00, 0.90, 0.95, 0.90, 1.00, 0.90 |
| concepts/legacy-exporter-status <-> concepts/exporter-migration | factual-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 0.95, 1.00, 1.00, 1.00, 1.00 |
| events/march-outage-cause <-> events/march-outage-review | factual-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.90, 0.95, 0.95, 0.95, 0.95 |
| concepts/personalized-recommendation <-> concepts/non-personalized-recommendation | antonym | `consistent` | `consistent` | 0.93 | 0.93 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 0.95 |
| concepts/synchronous-replication <-> concepts/asynchronous-replication | antonym | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/allowlist-filtering <-> concepts/denylist-filtering | antonym | `consistent` | `contradicts` | 0.00 | 1.00 | 0.95, 0.95, 1.00, 1.00, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 1.00, 0.95, 0.95, 0.95, 0.95 |
| concepts/optimistic-locking <-> concepts/pessimistic-locking | antonym | `consistent` | `contradicts` | 0.40 | 0.60 | 1.00, 0.95, 1.00, 0.95, 0.90, 0.95, 0.95, 0.95, 1.00, 1.00, 1.00, 0.95, 0.95, 1.00, 0.95 |
| concepts/supervised-learning <-> concepts/unsupervised-learning | antonym | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/retry-budget <-> concepts/request-scheduler | plain-consistent | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
| concepts/bundle-format <-> concepts/concept-document | plain-consistent | `consistent` | `consistent` | 1.00 | 1.00 | 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 0.95, 1.00, 1.00, 0.95, 0.95, 1.00 |
| concepts/client-default-timeout <-> concepts/client-timeout-behavior | definitional-contradiction | `contradicts` | `contradicts` | 1.00 | 1.00 | 1.00, 1.00, 0.95, 0.95, 0.95, 1.00, 1.00, 0.95, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00 |
