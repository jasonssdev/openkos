# `suggest_edge_types` eval — arm `model-llama3.1` (#508)

_Generated: 20260809T204041Z_ · model `llama3.1:8b` · **3 runs** over 17 labelled edges.

Labels are CONSTRUCTED, not adjudicated — see `fixtures.py`. Read accuracy as rubric-consistency, and trust **stability** when a label is arguable: it needs no labels at all.

| metric | value |
| --- | --- |
| type accuracy vs label | 0.45 |
| mean stability (modal share) | 0.76 |
| degraded replies | 0 of 51 |
| mean run latency | 20.0s |
| mean stated confidence, CORRECT answers | 0.00 |
| mean stated confidence, WRONG answers | 0.00 |

A threshold policy is only meaningful if the second number is clearly below the first. Equal values mean stated confidence carries no signal about correctness, and gating on it would automate the errors instead of catching them.

## Type distribution

| type | emissions | share |
| --- | --- | --- |
| `part_of` | 13 | 0.25 |
| `member_of` | 11 | 0.22 |
| `depends_on` | 10 | 0.20 |
| `related_to` | 8 | 0.16 |
| `caused_by` | 6 | 0.12 |
| `references` | 3 | 0.06 |

## Per edge

| pair | probes | expected | modal | acc | stab |
| --- | --- | --- | --- | --- | --- |
| concepts/retry-budget -> concepts/request-scheduler | part_of vs member_of | `part_of` | `depends_on` | 0.00 | 1.00 |
| concepts/nightly-backup-job -> concepts/scheduled-maintenance-jobs | part_of vs member_of | `member_of` | `related_to` | 0.00 | 0.67 |
| concepts/report-renderer -> concepts/template-cache | depends_on vs part_of | `depends_on` | `depends_on` | 0.67 | 0.67 |
| concepts/checkout-outage -> concepts/payment-gateway-migration | caused_by vs produced_by | `caused_by` | `caused_by` | 1.00 | 1.00 |
| concepts/quarterly-risk-report -> concepts/risk-committee | caused_by vs produced_by | `produced_by` | `member_of` | 0.00 | 1.00 |
| concepts/migration-runbook -> concepts/rollback-policy | references vs related_to | `references` | `depends_on` | 0.00 | 0.67 |
| concepts/onboarding-checklist -> concepts/incident-review-culture | the honest abstention | `related_to` | `part_of` | 0.00 | 1.00 |
| concepts/aortic-valve -> concepts/human-heart | part_of vs member_of (held out from the examples) | `part_of` | `part_of` | 1.00 | 1.00 |
| concepts/soprano-line -> concepts/voice-parts | part_of vs member_of (held out from the examples) | `member_of` | `member_of` | 0.67 | 0.67 |
| concepts/wal-segment -> concepts/write-ahead-log | part_of vs member_of | `part_of` | `part_of` | 1.00 | 1.00 |
| concepts/eu-west-replica -> concepts/read-replicas | part_of vs member_of | `member_of` | `related_to` | 0.33 | 0.33 |
| concepts/search-api -> concepts/ranking-service | depends_on vs part_of | `depends_on` | `depends_on` | 0.67 | 0.67 |
| concepts/data-loss-incident -> concepts/storage-upgrade | caused_by vs produced_by | `caused_by` | `caused_by` | 1.00 | 1.00 |
| concepts/architecture-decision-record -> concepts/platform-team | caused_by vs produced_by | `produced_by` | `member_of` | 0.00 | 1.00 |
| concepts/deploy-guide -> concepts/incident-severity-matrix | references vs related_to | `references` | `member_of` | 0.33 | 0.33 |
| concepts/hiring-loop -> concepts/documentation-style | the honest abstention | `related_to` | `related_to` | 0.33 | 0.33 |
| concepts/cost-dashboard -> concepts/oncall-rotation | the honest abstention | `related_to` | `related_to` | 0.67 | 0.67 |
