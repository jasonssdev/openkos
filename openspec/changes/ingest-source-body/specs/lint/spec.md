# Delta for Lint

## MODIFIED Requirements

### Requirement: Stale-Stamp Scan

`openkos lint` MUST scan concept bodies for inline `(as of YYYY-MM-DD)`
stamps and flag any stamp older than the configured `freshness_window`
(default `7d`) as a stale-stamp finding. The scan MUST read only inline
body text, never the `freshness` field, EXCEPT that the scan MUST skip
entirely any concept whose `freshness` field is `snapshot` — such concepts
(as produced by `openkos ingest`) embed verbatim source text that MAY
coincidentally contain an `(as of ...)`-shaped string, and that text is
not a maintained freshness stamp.

(Previously: relied on the assumption that `freshness: snapshot` concepts
"carry no `(as of ...)` stamp by design"; that assumption becomes false
once `ingest` embeds real source text, so this change makes the skip
explicit rather than incidental.)

#### Scenario: Stale stamp is flagged

- GIVEN a non-snapshot concept body containing `(as of YYYY-MM-DD)` older
  than the configured `freshness_window`
- WHEN `openkos lint` runs
- THEN the concept is reported as a stale-stamp finding

#### Scenario: Fresh stamp is not flagged

- GIVEN a non-snapshot concept body containing `(as of YYYY-MM-DD)` within
  the configured `freshness_window`
- WHEN `openkos lint` runs
- THEN the concept is NOT reported as a stale-stamp finding

#### Scenario: Pure-ingest bundle produces zero stale findings

- GIVEN a bundle containing only `freshness: snapshot` Source concepts
  produced by `openkos ingest`
- WHEN `openkos lint` runs
- THEN it reports zero stale-stamp findings, regardless of any
  `(as of ...)`-shaped text embedded in their bodies

#### Scenario: Snapshot concept with an embedded stamp-shaped string is not flagged

- GIVEN a `freshness: snapshot` Source concept whose embedded verbatim
  content contains text matching `(as of YYYY-MM-DD)`
- WHEN `openkos lint` runs
- THEN no stale-stamp finding is reported for that concept
