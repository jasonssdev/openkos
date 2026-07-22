# Delta for Lint

## MODIFIED Requirements

### Requirement: Stale-Stamp Scan

`openkos lint` MUST scan concept bodies for inline `(as of YYYY-MM-DD)`
stamps and flag any stamp older than that concept's volatility-resolved
stale window (per the `concept-volatility` capability's precedence:
per-concept `volatility` → per-type registry default → global
`freshness_window` fallback) as a stale-stamp finding. `static`-tier
concepts MUST NEVER be flagged, regardless of stamp age. The scan MUST read
only inline body text for the stamp itself, never the `freshness` field for
the stamp's age, EXCEPT that the scan MUST skip entirely any concept whose
`freshness` field is `snapshot` — independent of that concept's resolved
volatility tier.
(Previously: a single global `freshness_window` applied uniformly to every
non-snapshot concept, with no per-type or per-concept variation.)

#### Scenario: Stale stamp beyond the resolved window is flagged

- GIVEN a non-snapshot `slow`-tier concept body containing
  `(as of YYYY-MM-DD)` older than its resolved window
- WHEN `openkos lint` runs
- THEN the concept is reported as a stale-stamp finding

#### Scenario: Fresh stamp within the resolved window is not flagged

- GIVEN a non-snapshot concept body containing `(as of YYYY-MM-DD)` within
  its resolved window
- WHEN `openkos lint` runs
- THEN the concept is NOT reported as a stale-stamp finding

#### Scenario: static-tier concept is never flagged

- GIVEN a `static`-tier concept (by override or type default, e.g. `Place`)
  with a body stamp far older than any configured window
- WHEN `openkos lint` runs
- THEN the concept is NOT reported as a stale-stamp finding

#### Scenario: Per-concept override wins over type default

- GIVEN a `Procedure` concept (type default `volatile`) with
  `volatility: static` and an old stamp
- WHEN `openkos lint` runs
- THEN the concept is NOT flagged, because the per-concept override takes
  precedence over the type default

#### Scenario: Type default wins over the global fallback

- GIVEN a concept with no `volatility` field whose type default is `slow`,
  and a global `freshness_window` shorter than the `slow`-tier window
- WHEN `openkos lint` runs
- THEN the `slow`-tier window is used, not the global fallback

#### Scenario: Pure-ingest bundle produces zero stale findings

- GIVEN a bundle containing only `freshness: snapshot` Source concepts
- WHEN `openkos lint` runs
- THEN it reports zero stale-stamp findings, regardless of resolved
  volatility tier or any `(as of ...)`-shaped text embedded in their bodies

#### Scenario: Snapshot concept with an embedded stamp-shaped string is not flagged

- GIVEN a `freshness: snapshot` concept whose embedded verbatim content
  contains text matching `(as of YYYY-MM-DD)`
- WHEN `openkos lint` runs
- THEN no stale-stamp finding is reported, regardless of its resolved
  volatility tier

#### Scenario: Unresolvable volatility still degrades and never raises

- GIVEN a concept whose type is absent from the registry and whose
  `volatility` field is invalid
- WHEN `openkos lint` runs
- THEN lint does not raise, and the concept's window resolves to the global
  `freshness_window` fallback

## Non-Goals Update

Replace the existing Non-Goals clause:

> volatility classification via the `freshness` field (lint never reads it)

with:

> volatility classification via the `freshness` field remains out of
> scope — `freshness` stays a binary snapshot/non-snapshot skip flag,
> orthogonal to volatility; volatility classification is instead read from
> the concept's `volatility` field and per-type registry default (see the
> `concept-volatility` capability), applied only to resolve each concept's
> stale-stamp window.
