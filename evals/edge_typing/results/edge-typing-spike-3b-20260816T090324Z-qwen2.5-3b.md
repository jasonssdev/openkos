# `suggest_edge_types` eval — arm `spike-3b` (#508)

_Generated: 20260816T090324Z_ · model `qwen2.5:3b` · **15 runs** over 23 labelled edges.

Labels are CONSTRUCTED, not adjudicated — see `fixtures.py`. Read accuracy as rubric-consistency, and trust **stability** when a label is arguable: it needs no labels at all.

| metric | value |
| --- | --- |
| type accuracy vs label | 0.39 |
| mean stability (modal share) | 0.81 |
| degraded replies | 0 of 345 |
| mean run latency | 11.6s |
| **direction-trap hits (reversed probes)** | **43 of 90 (0.48)** |

_Two stated-confidence rows and the paragraph interpreting them were removed from this stored report by #740: `EdgeSuggestion` carries no `confidence` field, so both could only ever print a structural `0.00`. The per-edge `confidences` arrays in the `runs-*.json` beside this file are unchanged._

## Type distribution

| type | emissions | share |
| --- | --- | --- |
| `related_to` | 99 | 0.29 |
| `produced_by` | 93 | 0.27 |
| `part_of` | 68 | 0.20 |
| `caused_by` | 45 | 0.13 |
| `references` | 22 | 0.06 |
| `depends_on` | 15 | 0.04 |
| `member_of` | 3 | 0.01 |

## Per edge

| pair | probes | expected | modal | acc | stab |
| --- | --- | --- | --- | --- | --- |
| concepts/retry-budget -> concepts/request-scheduler | part_of vs member_of | `part_of` | `related_to` | 0.07 | 0.67 |
| concepts/nightly-backup-job -> concepts/scheduled-maintenance-jobs | part_of vs member_of | `member_of` | `depends_on` | 0.00 | 0.60 |
| concepts/report-renderer -> concepts/template-cache | depends_on vs part_of | `depends_on` | `produced_by` | 0.00 | 1.00 |
| concepts/checkout-outage -> concepts/payment-gateway-migration | caused_by vs produced_by | `caused_by` | `caused_by` | 1.00 | 1.00 |
| concepts/quarterly-risk-report -> concepts/risk-committee | caused_by vs produced_by | `produced_by` | `produced_by` | 1.00 | 1.00 |
| concepts/migration-runbook -> concepts/rollback-policy | references vs related_to | `references` | `produced_by` | 0.07 | 0.67 |
| concepts/onboarding-checklist -> concepts/incident-review-culture | the honest abstention | `related_to` | `related_to` | 1.00 | 1.00 |
| concepts/aortic-valve -> concepts/human-heart | part_of vs member_of (held out from the examples) | `part_of` | `part_of` | 1.00 | 1.00 |
| concepts/soprano-line -> concepts/voice-parts | part_of vs member_of (held out from the examples) | `member_of` | `related_to` | 0.00 | 1.00 |
| concepts/wal-segment -> concepts/write-ahead-log | part_of vs member_of | `part_of` | `produced_by` | 0.40 | 0.60 |
| concepts/eu-west-replica -> concepts/read-replicas | part_of vs member_of | `member_of` | `part_of` | 0.00 | 0.60 |
| concepts/search-api -> concepts/ranking-service | depends_on vs part_of | `depends_on` | `depends_on` | 0.40 | 0.40 |
| concepts/data-loss-incident -> concepts/storage-upgrade | caused_by vs produced_by | `caused_by` | `caused_by` | 1.00 | 1.00 |
| concepts/architecture-decision-record -> concepts/platform-team | caused_by vs produced_by | `produced_by` | `produced_by` | 0.67 | 0.67 |
| concepts/deploy-guide -> concepts/incident-severity-matrix | references vs related_to | `references` | `related_to` | 0.13 | 0.87 |
| concepts/hiring-loop -> concepts/documentation-style | the honest abstention | `related_to` | `related_to` | 1.00 | 1.00 |
| concepts/cost-dashboard -> concepts/oncall-rotation | the honest abstention | `related_to` | `references` | 0.47 | 0.53 |
| concepts/scheduled-maintenance-jobs -> concepts/nightly-backup-job | direction: collection -> member | `related_to` | `related_to` | 0.47 | 0.47 |
| concepts/read-replicas -> concepts/eu-west-replica | direction: collection -> member | `related_to` | `part_of` | 0.00 | 1.00 |
| concepts/voice-parts -> concepts/soprano-line | direction: collection -> member (held out from the examples) | `related_to` | `part_of` | 0.00 | 0.80 |
| concepts/risk-committee -> concepts/quarterly-risk-report | direction: author -> artifact | `references` | `produced_by` | 0.00 | 1.00 |
| concepts/platform-team -> concepts/architecture-decision-record | direction: author -> artifact | `references` | `produced_by` | 0.27 | 0.67 |
| concepts/payment-gateway-migration -> concepts/checkout-outage | direction: cause -> outcome | `references` | `caused_by` | 0.00 | 1.00 |
