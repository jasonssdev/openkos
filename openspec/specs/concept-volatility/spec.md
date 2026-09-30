# Concept Volatility Specification

## Purpose

Classifies every concept into one of three fixed knowledge-volatility
tiers — `static`, `slow`, `volatile` — so downstream freshness logic (the
`lint` capability) can apply a stale window proportional to how quickly a
concept's kind of knowledge decays, instead of one fixed global window for
every document.

## Non-Goals

This spec does not define: LLM-suggested tier windows or an LLM-facing
`suggest-volatility` verb (see the `volatility-suggestion` capability);
contradiction detection (S3); a reconcile/write workflow (S4); any change to
`freshness: snapshot` semantics, which remains an orthogonal skip flag,
never a volatility signal.

## Requirements

### Requirement: Fixed Three-Tier Volatility Taxonomy

The system MUST classify every resolvable concept into exactly one of three
tiers: `static`, `slow`, `volatile`. No other tier value is valid.

#### Scenario: Only the three defined tiers are valid

- GIVEN any volatility resolution (per-concept, per-type, or fallback)
- WHEN a tier is produced
- THEN the tier is exactly one of `static`, `slow`, `volatile`

### Requirement: Per-Concept `volatility` Frontmatter Override

The system MUST support an optional `volatility:` frontmatter field on any
concept, holding one of the three tier values, distinct from and orthogonal
to `freshness`. WHEN absent, the field MUST NOT be treated as an error;
resolution MUST fall through to the per-type default.

#### Scenario: Explicit per-concept override is honored

- GIVEN a concept with `volatility: volatile` whose type default is `slow`
- WHEN its window is resolved
- THEN the `volatile`-tier window is used, not the type default

#### Scenario: Absent field falls through to type default

- GIVEN a concept with no `volatility` field
- WHEN its window is resolved
- THEN resolution proceeds to the concept's per-type default tier

### Requirement: Per-Type Default Volatility Registry

Each `ObjectType` in the registry MUST carry a default volatility tier:
`static` for `Place`, `Event`, `Decision`, `Source`; `slow` for `Concept`,
`Entity`, `Person`, `Organization`; `volatile` for `Procedure`, `Project`.

#### Scenario: Type default applies when no override is present

- GIVEN a `Procedure` concept with no `volatility` field
- WHEN its window is resolved
- THEN the `volatile`-tier default for `Procedure` is used

### Requirement: `type_tiers` Config Override Layer

The system MUST support an optional `type_tiers:` map in `openkos.yaml`
(concept-type-name → tier value), read-only, absent-default `{}`. An entry
MUST be ignored — resolution falls through to the next precedence step,
never raising — if EITHER its type name is unknown (absent from the
registry) OR its tier value is not one of `static`, `slow`, `volatile`.

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

### Requirement: `volatility_windows` Config Maps A Tier To A Window

The system MUST support an optional `volatility_windows:` map in
`openkos.yaml` (tier name → duration), read-only, absent-default `{}`, that
sets the stale-stamp window each tier resolves to. Only the `slow` and
`volatile` keys are read; `static` has no window (it is never flagged) and
any other key is ignored. WHEN a tier's key is absent, its packaged default
MUST apply: `90d` for `slow` and `7d` for `volatile`. A duration MUST be a
positive integer followed by `d` (days) or `w` (weeks, seven days each),
with surrounding whitespace tolerated. WHEN a present value is not a valid
duration (a non-string, a zero or negative count, or any other shape), the
system MUST NOT raise: that tier MUST resolve to the packaged default
freshness window, `7d`, and `lint` MUST report a notice naming the invalid
value. WHEN `volatility_windows` is not a mapping at all (a list or a
scalar), it MUST be treated as `{}`.

#### Scenario: A configured tier window replaces the packaged default

- GIVEN `volatility_windows: {slow: 30d}` and a `Concept` (registry tier
  `slow`) with no `volatility` frontmatter
- WHEN its window is resolved
- THEN the window is 30 days, and the `volatile` window is still 7 days

#### Scenario: Absent map uses the packaged tier defaults

- GIVEN `openkos.yaml` has no `volatility_windows` key
- WHEN a `slow`-tier and a `volatile`-tier concept have their windows
  resolved
- THEN the windows are 90 days and 7 days respectively

#### Scenario: An invalid duration degrades with a notice, never raises

- GIVEN `volatility_windows: {volatile: soon}` and a `volatile`-tier concept
- WHEN its window is resolved
- THEN the window is the packaged default freshness window (7 days), a
  notice names the invalid value, and no exception is raised

#### Scenario: A `static` key is not a window

- GIVEN `volatility_windows: {static: 1d}` and a `static`-tier concept
- WHEN freshness is evaluated for that concept
- THEN it is never flagged stale

### Requirement: Deterministic, Never-Raising Window Resolution

Resolving a concept's effective volatility tier and window MUST follow the
precedence: per-concept `volatility` override → `type_tiers` config
override → per-type registry default → global `freshness_window` fallback.
The resolved tier MUST then map to its window: `static` → never flagged;
`slow` and `volatile` → the tier's `volatility_windows` entry (or its
packaged default); the fallback tier → the global `freshness_window`.
Resolution MUST be a pure, deterministic function of concept data, an
injected clock, and config; it MUST NOT raise on an unknown type, an
invalid `volatility` value, an invalid or unknown `type_tiers` entry, an
invalid `volatility_windows` duration, or missing config — each such case
MUST degrade to the next step in the precedence chain.

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
