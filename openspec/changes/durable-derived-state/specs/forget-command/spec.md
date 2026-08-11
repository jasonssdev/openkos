# Delta for Forget Command

Slice 1a.

## ADDED Requirements

### Requirement: Inbound-Reference Scan Excludes Ledger Storage

`forget`'s inbound-reference and unverifiable-referrer detection (shared
with `merge`) MUST NOT treat any bytes under `bundle/.state/ledger/` as a
reference source. A purge-set member's id appearing inside a ledger sidecar
entry (e.g. as part of an `absorbed_snapshot` or `survivor_before` field)
MUST NOT count as an inbound markdown link, typed relation, or
unverifiable-referrer match, and MUST NOT contribute to the
external-reference refusal.

#### Scenario: A ledger snapshot mentioning the purge-set id is not a reference
- GIVEN a survivor's ledger sidecar entry embeds a historical snapshot that
  contains the purge-set member's id or a link to it
- WHEN `openkos forget <concept-id>` runs
- THEN that sidecar entry is not surfaced as an inbound reference and does
  not contribute to the refusal count

#### Scenario: A genuine bundle-file reference is still detected
- GIVEN a concept file outside `bundle/.state/` holds a markdown link to
  the purge-set member, alongside an unrelated ledger sidecar entry that
  also mentions the id
- WHEN `openkos forget <concept-id>` runs
- THEN only the bundle-file reference is surfaced and counted; the ledger
  mention is excluded

### Requirement: Deletion Sweep Includes Ledger Storage

`forget`'s Phase B deletion/redaction sweep MUST cover
`bundle/.state/ledger/`: any ledger sidecar entry whose snapshot fields
(`absorbed_snapshot`, `survivor_before`, `index_before`, `log_before`, or
any `relation_rewrites`/`provenance_rewrites` snapshot) contain a
purge-set member's body MUST be redacted or removed as part of the same
Phase B write that deletes the purge-set member's own file, so that a
concept's content does not survive `forget` merely because it was
previously absorbed into (or is the survivor of) a merge.

#### Scenario: Forgetting an absorbed concept's historical snapshot is swept
- GIVEN a purge-set member was absorbed by a prior merge and its
  pre-merge body is preserved in the survivor's ledger sidecar as
  `absorbed_snapshot`
- WHEN `openkos forget <concept-id>` completes
- THEN that snapshot no longer contains the forgotten concept's body under
  `bundle/.state/ledger/`

#### Scenario: Forgetting a survivor sweeps its own ledger entries
- GIVEN the purge-set member is a merge survivor with its own ledger
  sidecar under `bundle/.state/ledger/`
- WHEN `openkos forget <concept-id>` completes
- THEN that sidecar is swept as part of the same Phase B write, not left
  behind
