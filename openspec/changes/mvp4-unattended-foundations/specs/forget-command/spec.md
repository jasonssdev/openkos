# Delta for Forget Command

## ADDED Requirements

### Requirement: Deletion Sweep Includes The Pending-Work Queue

`openkos forget` MUST delete, from `.openkos/findings.db`, every
pending-work queue row whose target ids or decision key name a purge-set
member, or whose payload quotes text from a purge-set member, under the same
erasure discipline as the other `findings.db` tenants (no freelist-recoverable
pages, no residual WAL images). The sweep MUST match on every field that
names a concept, not only on a pair field. A missing store or table is a
no-op and is never created; an unreadable store degrades to a stderr warning
naming the residue and remedy.

#### Scenario: Forgetting a concept removes its queue rows

- GIVEN an open identity row whose member set includes `<id>` and a
  `watch_refusal` row keyed by `<id>`
- WHEN `openkos forget <id>` completes Phase B
- THEN neither row exists and no text from `<id>` is recoverable from the
  file's bytes

#### Scenario: An unrelated row survives

- GIVEN an open row naming only concepts outside the purge set
- WHEN `openkos forget <id>` completes
- THEN that row is unchanged
