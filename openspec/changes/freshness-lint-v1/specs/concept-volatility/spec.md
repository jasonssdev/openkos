# Concept Volatility Specification

## Purpose

Classifies every concept into one of three fixed knowledge-volatility
tiers — `static`, `slow`, `volatile` — so downstream freshness logic (the
`lint` capability) can apply a stale window proportional to how quickly a
concept's kind of knowledge decays, instead of one fixed global window for
every document.

## Non-Goals

This spec does not define: LLM-suggested or learned windows (S2);
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

### Requirement: Deterministic, Never-Raising Window Resolution

Resolving a concept's effective volatility tier and window MUST follow the
precedence: per-concept `volatility` override → per-type registry default →
global `freshness_window` fallback. Resolution MUST be a pure, deterministic
function of concept data, an injected clock, and config; it MUST NOT raise
on an unknown type, an invalid `volatility` value, or missing config — each
such case MUST degrade to the next step in the precedence chain.

#### Scenario: Unknown or invalid volatility degrades without raising

- GIVEN a concept whose `volatility` value is not one of the three valid
  tiers, or whose type is absent from the registry
- WHEN its window is resolved
- THEN resolution degrades to the next precedence step and does not raise

#### Scenario: No override and no type match falls back to the global window

- GIVEN a concept with no `volatility` field and a type absent from the
  registry
- WHEN its window is resolved
- THEN the global `freshness_window` fallback value is used
