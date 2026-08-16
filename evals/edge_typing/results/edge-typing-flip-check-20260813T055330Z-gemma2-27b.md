# `suggest_edge_types` eval — arm `flip-check` (#508)

_Generated: 20260813T055330Z_ · model `gemma2:27b` · **3 runs** over 23 labelled edges.

Labels are CONSTRUCTED, not adjudicated — see `fixtures.py`. Read accuracy as rubric-consistency, and trust **stability** when a label is arguable: it needs no labels at all.

| metric | value |
| --- | --- |
| type accuracy vs label | 0.35 |
| mean stability (modal share) | 0.88 |
| degraded replies | 0 of 69 |
| mean run latency | 335.9s |
| **direction-trap hits (reversed probes)** | **1 of 18 (0.06)** |

_Two stated-confidence rows and the paragraph interpreting them were removed from this stored report by #740: `EdgeSuggestion` carries no `confidence` field, so both could only ever print a structural `0.00`. The per-edge `confidences` arrays in the `runs-*.json` beside this file are unchanged._

## Type distribution

| type | emissions | share |
| --- | --- | --- |
| `related_to` | 52 | 0.75 |
| `depends_on` | 6 | 0.09 |
| `member_of` | 5 | 0.07 |
| `references` | 4 | 0.06 |
| `caused_by` | 1 | 0.01 |
| `produced_by` | 1 | 0.01 |

## Per edge

| pair | probes | expected | modal | acc | stab |
| --- | --- | --- | --- | --- | --- |
| concepts/retry-budget -> concepts/request-scheduler | part_of vs member_of | `part_of` | `related_to` | 0.00 | 1.00 |
| concepts/nightly-backup-job -> concepts/scheduled-maintenance-jobs | part_of vs member_of | `member_of` | `related_to` | 0.33 | 0.67 |
| concepts/report-renderer -> concepts/template-cache | depends_on vs part_of | `depends_on` | `depends_on` | 1.00 | 1.00 |
| concepts/checkout-outage -> concepts/payment-gateway-migration | caused_by vs produced_by | `caused_by` | `related_to` | 0.00 | 1.00 |
| concepts/quarterly-risk-report -> concepts/risk-committee | caused_by vs produced_by | `produced_by` | `related_to` | 0.00 | 1.00 |
| concepts/migration-runbook -> concepts/rollback-policy | references vs related_to | `references` | `related_to` | 0.00 | 1.00 |
| concepts/onboarding-checklist -> concepts/incident-review-culture | the honest abstention | `related_to` | `related_to` | 0.67 | 0.67 |
| concepts/aortic-valve -> concepts/human-heart | part_of vs member_of (held out from the examples) | `part_of` | `related_to` | 0.00 | 1.00 |
| concepts/soprano-line -> concepts/voice-parts | part_of vs member_of (held out from the examples) | `member_of` | `related_to` | 0.33 | 0.67 |
| concepts/wal-segment -> concepts/write-ahead-log | part_of vs member_of | `part_of` | `related_to` | 0.00 | 1.00 |
| concepts/eu-west-replica -> concepts/read-replicas | part_of vs member_of | `member_of` | `related_to` | 0.33 | 0.67 |
| concepts/search-api -> concepts/ranking-service | depends_on vs part_of | `depends_on` | `related_to` | 0.33 | 0.67 |
| concepts/data-loss-incident -> concepts/storage-upgrade | caused_by vs produced_by | `caused_by` | `related_to` | 0.00 | 1.00 |
| concepts/architecture-decision-record -> concepts/platform-team | caused_by vs produced_by | `produced_by` | `member_of` | 0.00 | 0.67 |
| concepts/deploy-guide -> concepts/incident-severity-matrix | references vs related_to | `references` | `references` | 1.00 | 1.00 |
| concepts/hiring-loop -> concepts/documentation-style | the honest abstention | `related_to` | `depends_on` | 0.00 | 0.67 |
| concepts/cost-dashboard -> concepts/oncall-rotation | the honest abstention | `related_to` | `related_to` | 1.00 | 1.00 |
| concepts/scheduled-maintenance-jobs -> concepts/nightly-backup-job | direction: collection -> member | `related_to` | `related_to` | 1.00 | 1.00 |
| concepts/read-replicas -> concepts/eu-west-replica | direction: collection -> member | `related_to` | `related_to` | 1.00 | 1.00 |
| concepts/voice-parts -> concepts/soprano-line | direction: collection -> member (held out from the examples) | `related_to` | `related_to` | 1.00 | 1.00 |
| concepts/risk-committee -> concepts/quarterly-risk-report | direction: author -> artifact | `references` | `related_to` | 0.00 | 1.00 |
| concepts/platform-team -> concepts/architecture-decision-record | direction: author -> artifact | `references` | `related_to` | 0.00 | 0.67 |
| concepts/payment-gateway-migration -> concepts/checkout-outage | direction: cause -> outcome | `references` | `related_to` | 0.00 | 1.00 |
