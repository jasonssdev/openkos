# `suggest_edge_types` eval — arm `cross-type-990` (#508)

_Generated: 20260915T050842Z_ · model `qwen3:8b` · **5 runs** over 29 labelled edges.

Generation ceiling `8192` · context window `12288`.

Rationale language pinned: **none** — the baseline prompt, byte-identical to what ships (#812).

Labels are CONSTRUCTED, not adjudicated — see `fixtures.py`. Read accuracy as rubric-consistency, and trust **stability** when a label is arguable: it needs no labels at all.

| metric | value |
| --- | --- |
| type accuracy vs label | 0.35 |
| mean stability (modal share) | 0.99 |
| degraded replies | 0 of 145 |
| mean run latency | 43.0s |
| **direction-trap hits (reversed probes)** | **5 of 45 (0.11)** |

**No stated-confidence metric is reported here, by construction (#740).** `EdgeSuggestion` carries no `confidence` field and the #508 investigation concluded it should not gain one, so every arm run so far reads the `getattr` default. Until #740 this report printed `0.00` for CORRECT and `0.00` for WRONG answers, which reads like a calibration finding and is instead a column that could not vary. The per-edge `confidences` arrays stay in `runs-*.json` as the seam for an arm that re-adds the field; the sibling `evals/contradictions/` column, which does measure a real reply field, is unaffected.

## By type regime (#990)

Every inversion reported against a real bundle joins two objects of DIFFERENT types (`organizations/... -> people/...`, `concepts/... -> projects/...`). Before #990 this corpus was entirely `Concept`, so that regime was absent and a rule reading the two ends' types had nothing here to move. These rows are split so it cannot move without the report saying so.

| regime | edges | accuracy | trap hits |
| --- | --- | --- | --- |
| same-type | 23 | 0.40 (46 of 115) | 5 of 30 (0.17) |
| cross-type | 6 | 0.17 (5 of 30) | 0 of 15 (0.00) |

## Type distribution

| type | emissions | share |
| --- | --- | --- |
| `related_to` | 100 | 0.69 |
| `part_of` | 24 | 0.17 |
| `produced_by` | 10 | 0.07 |
| `member_of` | 6 | 0.04 |
| `references` | 5 | 0.03 |

## Per edge

| pair | probes | expected | modal | acc | stab |
| --- | --- | --- | --- | --- | --- |
| concepts/retry-budget -> concepts/request-scheduler | part_of vs member_of | `part_of` | `related_to` | 0.00 | 1.00 |
| concepts/nightly-backup-job -> concepts/scheduled-maintenance-jobs | part_of vs member_of | `member_of` | `part_of` | 0.20 | 0.80 |
| concepts/report-renderer -> concepts/template-cache | depends_on vs part_of | `depends_on` | `references` | 0.00 | 1.00 |
| concepts/checkout-outage -> concepts/payment-gateway-migration | caused_by vs produced_by | `caused_by` | `related_to` | 0.00 | 1.00 |
| concepts/quarterly-risk-report -> concepts/risk-committee | caused_by vs produced_by | `produced_by` | `produced_by` | 1.00 | 1.00 |
| concepts/migration-runbook -> concepts/rollback-policy | references vs related_to | `references` | `related_to` | 0.00 | 1.00 |
| concepts/onboarding-checklist -> concepts/incident-review-culture | the honest abstention | `related_to` | `related_to` | 1.00 | 1.00 |
| concepts/aortic-valve -> concepts/human-heart | part_of vs member_of (held out from the examples) | `part_of` | `part_of` | 1.00 | 1.00 |
| concepts/soprano-line -> concepts/voice-parts | part_of vs member_of (held out from the examples) | `member_of` | `member_of` | 1.00 | 1.00 |
| concepts/wal-segment -> concepts/write-ahead-log | part_of vs member_of | `part_of` | `part_of` | 1.00 | 1.00 |
| concepts/eu-west-replica -> concepts/read-replicas | part_of vs member_of | `member_of` | `part_of` | 0.00 | 1.00 |
| concepts/search-api -> concepts/ranking-service | depends_on vs part_of | `depends_on` | `related_to` | 0.00 | 1.00 |
| concepts/data-loss-incident -> concepts/storage-upgrade | caused_by vs produced_by | `caused_by` | `related_to` | 0.00 | 1.00 |
| concepts/architecture-decision-record -> concepts/platform-team | caused_by vs produced_by | `produced_by` | `related_to` | 0.00 | 1.00 |
| concepts/deploy-guide -> concepts/incident-severity-matrix | references vs related_to | `references` | `related_to` | 0.00 | 1.00 |
| concepts/hiring-loop -> concepts/documentation-style | the honest abstention | `related_to` | `related_to` | 1.00 | 1.00 |
| concepts/cost-dashboard -> concepts/oncall-rotation | the honest abstention | `related_to` | `related_to` | 1.00 | 1.00 |
| concepts/scheduled-maintenance-jobs -> concepts/nightly-backup-job | direction: collection -> member | `related_to` | `related_to` | 1.00 | 1.00 |
| concepts/read-replicas -> concepts/eu-west-replica | direction: collection -> member | `related_to` | `part_of` | 0.00 | 1.00 |
| concepts/voice-parts -> concepts/soprano-line | direction: collection -> member (held out from the examples) | `related_to` | `related_to` | 1.00 | 1.00 |
| concepts/risk-committee -> concepts/quarterly-risk-report | direction: author -> artifact | `references` | `produced_by` | 0.00 | 1.00 |
| concepts/platform-team -> concepts/architecture-decision-record | direction: author -> artifact | `references` | `related_to` | 0.00 | 1.00 |
| concepts/payment-gateway-migration -> concepts/checkout-outage | direction: cause -> outcome | `references` | `related_to` | 0.00 | 1.00 |
| people/dana-reyes -> organizations/platform-guild | cross-type: member -> collection (Person -> Organization) | `member_of` | `related_to` | 0.00 | 1.00 |
| organizations/platform-guild -> people/dana-reyes | cross-type direction: collection -> member (Organization -> Person) | `related_to` | `related_to` | 1.00 | 1.00 |
| concepts/sampling-policy -> projects/telemetry-rewrite | cross-type: artifact -> producer (Concept -> Project) | `produced_by` | `related_to` | 0.00 | 1.00 |
| projects/telemetry-rewrite -> concepts/sampling-policy | cross-type direction: producer -> artifact (Project -> Concept) | `references` | `related_to` | 0.00 | 1.00 |
| concepts/escalation-ladder -> people/marcus-oyelaran | cross-type: artifact -> author (Concept -> Person) | `produced_by` | `related_to` | 0.00 | 1.00 |
| people/marcus-oyelaran -> concepts/escalation-ladder | cross-type direction: author -> artifact (Person -> Concept) | `references` | `related_to` | 0.00 | 1.00 |
