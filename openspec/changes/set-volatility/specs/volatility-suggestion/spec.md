# Delta for Volatility Suggestion

## MODIFIED Requirements

### Requirement: Workspace-Gated, Read-Only Per-Type Suggestion

The system MUST provide a CLI verb, `suggest-volatility`, that requires an
active workspace and, for every concept type present in the bundle, prints
an LLM-suggested tier (one of `static`, `slow`, `volatile`) and a rationale.
The verb MUST perform ZERO writes to any bundle file, index, log, or config.
Output MUST be a plain stdout report ending with a hint to run `openkos
set-volatility <ConceptType> <tier>` to apply an accepted suggestion.
(Previously: the trailing hint told the user to hand-edit `type_tiers:` in
`openkos.yaml` directly; `suggest-volatility` itself still performs zero
writes.)

#### Scenario: Verb suggests a tier per type

- GIVEN a bundle containing `Person` and `Procedure` concepts
- WHEN `suggest-volatility` runs inside the workspace
- THEN it prints one suggested tier and rationale for each type present
- AND the report ends with a hint to run `openkos set-volatility
  <ConceptType> <tier>`

#### Scenario: Verb requires an active workspace

- GIVEN no workspace is active
- WHEN `suggest-volatility` runs
- THEN it fails with the standard `require_workspace` gate error, before any
  LLM call

#### Scenario: Verb performs zero writes

- GIVEN a bundle with multiple concept types
- WHEN `suggest-volatility` runs to completion
- THEN no bundle file, index, log, or `openkos.yaml` is modified on disk
