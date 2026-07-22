# Delta for Concept Volatility

## ADDED Requirements

### Requirement: `type_tiers` Config Override Layer

The system MUST support an optional `type_tiers:` map in `openkos.yaml`
(concept-type-name → tier value), read-only, absent-default `{}`. An entry
whose type name is unknown or whose tier value is not one of `static`,
`slow`, `volatile` MUST be ignored — resolution falls through to the next
precedence step — and MUST NOT raise.

#### Scenario: Valid `type_tiers` entry overrides the registry default

- GIVEN `type_tiers: {Person: volatile}` and `Person`'s registry default is
  `slow`
- WHEN a `Person` concept with no `volatility` frontmatter has its window
  resolved
- THEN the `volatile`-tier window is used, from `type_tiers`

#### Scenario: Invalid `type_tiers` entry is ignored, never raises

- GIVEN `type_tiers: {Person: bogus-tier}` or `type_tiers: {UnknownType:
  slow}`
- WHEN a concept of that type has its window resolved
- THEN the invalid entry is ignored and resolution falls through to the
  per-type registry default without raising

#### Scenario: Absent `type_tiers` reproduces exact S1 behavior

- GIVEN `openkos.yaml` has no `type_tiers` key (or it is empty `{}`)
- WHEN any concept's window is resolved
- THEN the result is identical to S1 behavior with no `type_tiers` step
  present

## MODIFIED Requirements

### Requirement: Deterministic, Never-Raising Window Resolution

Resolving a concept's effective volatility tier and window MUST follow the
precedence: per-concept `volatility` override → `type_tiers` config
override → per-type registry default → global `freshness_window` fallback.
Resolution MUST be a pure, deterministic function of concept data, an
injected clock, and config; it MUST NOT raise on an unknown type, an
invalid `volatility` value, an invalid or unknown `type_tiers` entry, or
missing config — each such case MUST degrade to the next step in the
precedence chain.
(Previously: precedence was per-concept `volatility` override → per-type
registry default → global `freshness_window` fallback, with no
config-layer override step.)

#### Scenario: Unknown or invalid volatility degrades without raising

- GIVEN a concept whose `volatility` value is not one of the three valid
  tiers, or whose type is absent from the registry
- WHEN its window is resolved
- THEN resolution degrades to the next precedence step and does not raise

#### Scenario: No override and no type match falls back to the global window

- GIVEN a concept with no `volatility` field, a type absent from the
  registry, and no matching `type_tiers` entry
- WHEN its window is resolved
- THEN the global `freshness_window` fallback value is used

#### Scenario: `type_tiers` override wins over registry default

- GIVEN a concept with no `volatility` frontmatter and a `type_tiers` entry
  for its type that differs from the registry default
- WHEN its window is resolved
- THEN the `type_tiers` tier is used, not the registry default

#### Scenario: `type_tiers` resolving to `static` is never flagged

- GIVEN a concept whose effective tier resolves to `static` via `type_tiers`
- WHEN freshness/staleness is evaluated for that concept
- THEN it is never flagged stale, identical to any other `static`
  resolution
