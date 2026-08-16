# `suggest_edge_types` eval — arm `baseline-23` (#508)

_Generated: 20260812T225503Z_ · model `gemma2:27b` · **3 runs** over 23 labelled edges.

Labels are CONSTRUCTED, not adjudicated — see `fixtures.py`. Read accuracy as rubric-consistency, and trust **stability** when a label is arguable: it needs no labels at all.

| metric | value |
| --- | --- |
| type accuracy vs label | 0.61 |
| mean stability (modal share) | 0.96 |
| degraded replies | 0 of 69 |
| mean run latency | 167.7s |
| **direction-trap hits (reversed probes)** | **17 of 18 (0.94)** |

_Two stated-confidence rows and the paragraph interpreting them were removed from this stored report by #740: `EdgeSuggestion` carries no `confidence` field, so both could only ever print a structural `0.00`. The per-edge `confidences` arrays in the `runs-*.json` beside this file are unchanged._

## Type distribution

| type | emissions | share |
| --- | --- | --- |
| `member_of` | 17 | 0.25 |
| `depends_on` | 11 | 0.16 |
| `produced_by` | 11 | 0.16 |
| `related_to` | 11 | 0.16 |
| `caused_by` | 9 | 0.13 |
| `part_of` | 7 | 0.10 |
| `references` | 3 | 0.04 |

## Per edge

| pair | probes | expected | modal | acc | stab |
| --- | --- | --- | --- | --- | --- |
| concepts/retry-budget -> concepts/request-scheduler | part_of vs member_of | `part_of` | `depends_on` | 0.00 | 1.00 |
| concepts/nightly-backup-job -> concepts/scheduled-maintenance-jobs | part_of vs member_of | `member_of` | `member_of` | 1.00 | 1.00 |
| concepts/report-renderer -> concepts/template-cache | depends_on vs part_of | `depends_on` | `depends_on` | 1.00 | 1.00 |
| concepts/checkout-outage -> concepts/payment-gateway-migration | caused_by vs produced_by | `caused_by` | `caused_by` | 1.00 | 1.00 |
| concepts/quarterly-risk-report -> concepts/risk-committee | caused_by vs produced_by | `produced_by` | `produced_by` | 1.00 | 1.00 |
| concepts/migration-runbook -> concepts/rollback-policy | references vs related_to | `references` | `related_to` | 0.00 | 1.00 |
| concepts/onboarding-checklist -> concepts/incident-review-culture | the honest abstention | `related_to` | `related_to` | 1.00 | 1.00 |
| concepts/aortic-valve -> concepts/human-heart | part_of vs member_of (held out from the examples) | `part_of` | `part_of` | 1.00 | 1.00 |
| concepts/soprano-line -> concepts/voice-parts | part_of vs member_of (held out from the examples) | `member_of` | `member_of` | 1.00 | 1.00 |
| concepts/wal-segment -> concepts/write-ahead-log | part_of vs member_of | `part_of` | `part_of` | 1.00 | 1.00 |
| concepts/eu-west-replica -> concepts/read-replicas | part_of vs member_of | `member_of` | `member_of` | 1.00 | 1.00 |
| concepts/search-api -> concepts/ranking-service | depends_on vs part_of | `depends_on` | `depends_on` | 1.00 | 1.00 |
| concepts/data-loss-incident -> concepts/storage-upgrade | caused_by vs produced_by | `caused_by` | `caused_by` | 1.00 | 1.00 |
| concepts/architecture-decision-record -> concepts/platform-team | caused_by vs produced_by | `produced_by` | `produced_by` | 0.67 | 0.67 |
| concepts/deploy-guide -> concepts/incident-severity-matrix | references vs related_to | `references` | `references` | 1.00 | 1.00 |
| concepts/hiring-loop -> concepts/documentation-style | the honest abstention | `related_to` | `depends_on` | 0.33 | 0.67 |
| concepts/cost-dashboard -> concepts/oncall-rotation | the honest abstention | `related_to` | `related_to` | 1.00 | 1.00 |
| concepts/scheduled-maintenance-jobs -> concepts/nightly-backup-job | direction: collection -> member | `related_to` | `member_of` | 0.00 | 1.00 |
| concepts/read-replicas -> concepts/eu-west-replica | direction: collection -> member | `related_to` | `member_of` | 0.00 | 0.67 |
| concepts/voice-parts -> concepts/soprano-line | direction: collection -> member (held out from the examples) | `related_to` | `member_of` | 0.00 | 1.00 |
| concepts/risk-committee -> concepts/quarterly-risk-report | direction: author -> artifact | `references` | `produced_by` | 0.00 | 1.00 |
| concepts/platform-team -> concepts/architecture-decision-record | direction: author -> artifact | `references` | `produced_by` | 0.00 | 1.00 |
| concepts/payment-gateway-migration -> concepts/checkout-outage | direction: cause -> outcome | `references` | `caused_by` | 0.00 | 1.00 |
