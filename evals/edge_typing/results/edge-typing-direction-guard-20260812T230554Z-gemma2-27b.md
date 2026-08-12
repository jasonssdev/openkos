# `suggest_edge_types` eval — arm `direction-guard` (#508)

_Generated: 20260812T230554Z_ · model `gemma2:27b` · **3 runs** over 23 labelled edges.

Labels are CONSTRUCTED, not adjudicated — see `fixtures.py`. Read accuracy as rubric-consistency, and trust **stability** when a label is arguable: it needs no labels at all.

| metric | value |
| --- | --- |
| type accuracy vs label | 0.55 |
| mean stability (modal share) | 0.91 |
| degraded replies | 0 of 69 |
| mean run latency | 182.8s |
| mean stated confidence, CORRECT answers | 0.00 |
| mean stated confidence, WRONG answers | 0.00 |
| **direction-trap hits (reversed probes)** | **17 of 18 (0.94)** |

A threshold policy is only meaningful if the second number is clearly below the first. Equal values mean stated confidence carries no signal about correctness, and gating on it would automate the errors instead of catching them.

## Type distribution

| type | emissions | share |
| --- | --- | --- |
| `member_of` | 19 | 0.28 |
| `depends_on` | 13 | 0.19 |
| `caused_by` | 10 | 0.14 |
| `produced_by` | 10 | 0.14 |
| `part_of` | 7 | 0.10 |
| `related_to` | 5 | 0.07 |
| `references` | 5 | 0.07 |

## Per edge

| pair | probes | expected | modal | acc | stab |
| --- | --- | --- | --- | --- | --- |
| concepts/retry-budget -> concepts/request-scheduler | part_of vs member_of | `part_of` | `depends_on` | 0.00 | 1.00 |
| concepts/nightly-backup-job -> concepts/scheduled-maintenance-jobs | part_of vs member_of | `member_of` | `member_of` | 1.00 | 1.00 |
| concepts/report-renderer -> concepts/template-cache | depends_on vs part_of | `depends_on` | `depends_on` | 1.00 | 1.00 |
| concepts/checkout-outage -> concepts/payment-gateway-migration | caused_by vs produced_by | `caused_by` | `caused_by` | 1.00 | 1.00 |
| concepts/quarterly-risk-report -> concepts/risk-committee | caused_by vs produced_by | `produced_by` | `produced_by` | 1.00 | 1.00 |
| concepts/migration-runbook -> concepts/rollback-policy | references vs related_to | `references` | `depends_on` | 0.00 | 0.67 |
| concepts/onboarding-checklist -> concepts/incident-review-culture | the honest abstention | `related_to` | `related_to` | 0.67 | 0.67 |
| concepts/aortic-valve -> concepts/human-heart | part_of vs member_of (held out from the examples) | `part_of` | `part_of` | 1.00 | 1.00 |
| concepts/soprano-line -> concepts/voice-parts | part_of vs member_of (held out from the examples) | `member_of` | `member_of` | 1.00 | 1.00 |
| concepts/wal-segment -> concepts/write-ahead-log | part_of vs member_of | `part_of` | `part_of` | 1.00 | 1.00 |
| concepts/eu-west-replica -> concepts/read-replicas | part_of vs member_of | `member_of` | `member_of` | 1.00 | 1.00 |
| concepts/search-api -> concepts/ranking-service | depends_on vs part_of | `depends_on` | `depends_on` | 1.00 | 1.00 |
| concepts/data-loss-incident -> concepts/storage-upgrade | caused_by vs produced_by | `caused_by` | `caused_by` | 1.00 | 1.00 |
| concepts/architecture-decision-record -> concepts/platform-team | caused_by vs produced_by | `produced_by` | `member_of` | 0.33 | 0.67 |
| concepts/deploy-guide -> concepts/incident-severity-matrix | references vs related_to | `references` | `references` | 1.00 | 1.00 |
| concepts/hiring-loop -> concepts/documentation-style | the honest abstention | `related_to` | `depends_on` | 0.33 | 0.67 |
| concepts/cost-dashboard -> concepts/oncall-rotation | the honest abstention | `related_to` | `references` | 0.33 | 0.67 |
| concepts/scheduled-maintenance-jobs -> concepts/nightly-backup-job | direction: collection -> member | `related_to` | `member_of` | 0.00 | 1.00 |
| concepts/read-replicas -> concepts/eu-west-replica | direction: collection -> member | `related_to` | `member_of` | 0.00 | 0.67 |
| concepts/voice-parts -> concepts/soprano-line | direction: collection -> member (held out from the examples) | `related_to` | `member_of` | 0.00 | 1.00 |
| concepts/risk-committee -> concepts/quarterly-risk-report | direction: author -> artifact | `references` | `produced_by` | 0.00 | 1.00 |
| concepts/platform-team -> concepts/architecture-decision-record | direction: author -> artifact | `references` | `produced_by` | 0.00 | 1.00 |
| concepts/payment-gateway-migration -> concepts/checkout-outage | direction: cause -> outcome | `references` | `caused_by` | 0.00 | 1.00 |
