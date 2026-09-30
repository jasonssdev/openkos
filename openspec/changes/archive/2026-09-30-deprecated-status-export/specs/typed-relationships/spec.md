# Delta for Typed Relationships

## ADDED Requirements

### Requirement: `relate` Of A `supersedes` Edge Writes The Deprecated-Status Export

WHEN `openkos relate <source> supersedes <target>` ADDS a new edge (not the
idempotent already-present case) and `source` and `target` differ, the
system MUST evaluate the deprecated-status export projection
(`deprecated-status-export`) for `target` over its post-write superseded
state, and write its outcome into `target`'s document in the SAME Phase B
as the edge, under the SAME drift guard (with `target`'s bytes added to its
baselines) and the SAME autocommit. The preview MUST name `target`'s status
change, or state that its own human-authored `status` is preserved when the
outcome is BLOCKED. No other relation type MUST cause `relate` to write any
`status` or `status_derived_from` field, and the vocabulary itself is
unchanged (`supersedes` stays an accepted, advisory-warned type).

#### Scenario: relate supersedes exports the target's status

- GIVEN concepts `a` and `b`, with `b` carrying `status: stable`
- WHEN `openkos relate a supersedes b --auto` runs
- THEN `a` gains `{target: b, type: supersedes}` and `b` carries
  `status: deprecated` and `status_derived_from: supersedes`, in the same
  commit

#### Scenario: relate of any other type writes no status

- GIVEN concepts `a` and `b`, with `b` carrying `status: stable`
- WHEN `openkos relate a references b --auto` runs
- THEN `b`'s bytes are unchanged

#### Scenario: An idempotent relate supersedes writes no status

- GIVEN `a` already holds `{target: b, type: supersedes}` and `b` carries
  `status: stable` (pre-existing drift)
- WHEN `openkos relate a supersedes b --auto` runs
- THEN `b`'s bytes are unchanged; the drift stays `repair`'s concern
