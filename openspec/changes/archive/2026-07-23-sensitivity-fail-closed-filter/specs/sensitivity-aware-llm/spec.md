# Sensitivity-Aware LLM Specification

## Purpose

The `sensitivity` frontmatter field (`public`/`private`/`confidential`,
default floor `private`) is written by ingest but has no reader today except
merge's high-water-mark recompute. No verb or `llm.chat` call site gates on
it. This spec makes sensitivity govern which concepts may reach `llm.chat`,
via one shared fail-closed predicate applied uniformly across all six call
sites: `adjudicate`, `contradictions`, `suggest-relations`,
`suggest-volatility`, `query`, `extract`.

## Non-Goals

Redaction (exclusion only); a new `max_send_sensitivity` config key
(rejected — threshold is fixed at confidential-only); per-source
`ingest --sensitivity` input; S4 export exclusion; any change to how
`sensitivity` is written or to merge's high-water-mark recompute.

## Requirements

### Requirement: Fail-Closed Sensitivity Resolution

The system MUST resolve each concept's effective sensitivity from its own
`sensitivity` frontmatter field. A concept MUST resolve to confidential
(blocked) WHEN the field is `"confidential"`, OR is missing, OR its
frontmatter fails to parse, OR the file cannot be read, OR the value is not
one of `public`/`private`/`confidential`. None of these fallback conditions
MAY raise an uncaught exception.

#### Scenario: Explicit confidential is blocked
- GIVEN a concept with `sensitivity: confidential`
- WHEN its effective sensitivity is resolved
- THEN it resolves to confidential (blocked)

#### Scenario: Missing, malformed, or unreadable fails closed
- GIVEN a concept file with no `sensitivity` field, OR unparseable
  frontmatter, OR a file that cannot be opened/read
- WHEN its effective sensitivity is resolved
- THEN it resolves to confidential (blocked), never an uncaught exception

#### Scenario: Unknown sensitivity value fails closed
- GIVEN a concept with `sensitivity: top-secret` (not one of the three
  known ranks)
- WHEN its effective sensitivity is resolved
- THEN it resolves to confidential (blocked)

### Requirement: Private and Public Pass Through Unchanged

A concept resolving to `private` or `public` MUST be sent to `llm.chat`
exactly as it would be without this filter.

#### Scenario: Private and public concepts reach llm.chat
- GIVEN concepts with `sensitivity: private` and `sensitivity: public`
- WHEN any of the six call sites processes them
- THEN both are sent unchanged

### Requirement: Uniform Enforcement Across All Six Call Sites

Every call site sending concept content to `llm.chat` — `adjudicate`,
`contradictions`, `suggest-relations`, `suggest-volatility`, `query`,
`extract` — MUST exclude any concept resolving to confidential before the
send. No call site MAY bypass this gate.

#### Scenario: Confidential excluded from adjudicate/contradictions/suggest-relations
- GIVEN a confidential concept is a candidate for `adjudicate`,
  `contradictions`, or `suggest-relations`
- WHEN the command runs without `--include-confidential`
- THEN it is excluded from the `llm.chat` payload

#### Scenario: Confidential excluded from suggest-volatility
- GIVEN a confidential concept is under consideration for
  `suggest-volatility`
- WHEN it runs without `--include-confidential`
- THEN it is excluded from the `llm.chat` payload

#### Scenario: Confidential excluded from query/answer
- GIVEN a confidential concept matches a question
- WHEN `query`/`answer` runs without `--include-confidential`
- THEN it is excluded from the fused hits fed to `llm.chat`

### Requirement: Extract Gates on the Workspace Sensitivity Floor

`extract` runs on raw source content prior to concept-bundling and has no
per-doc `sensitivity` value. The system MUST instead gate `extract`'s
`llm.chat` call on `cfg.default_sensitivity`: WHEN the floor is
`confidential`, `extract` MUST NOT call `llm.chat` at all; WHEN the floor is
`private` or `public`, `extract` proceeds unchanged.

#### Scenario: Confidential floor skips extract's llm.chat call
- GIVEN a workspace with `default_sensitivity: confidential`
- WHEN `extract` runs
- THEN it does not call `llm.chat`; this is a documented skip, not an error

#### Scenario: Private floor proceeds unchanged
- GIVEN a workspace with `default_sensitivity: private`
- WHEN `extract` runs
- THEN it calls `llm.chat` exactly as before this change

### Requirement: `--include-confidential` Escape Flag

Every `llm.chat`-calling command MUST offer an opt-in
`--include-confidential` flag that restores pre-filter, sensitivity-blind
behavior byte-for-byte. When absent, exclusion is the default — the
filtering resolution MUST still execute.

#### Scenario: Flag restores excluded concepts
- GIVEN a confidential concept that would otherwise be excluded from
  `query`
- WHEN `query --include-confidential` runs
- THEN it participates exactly as a private/public concept would

#### Scenario: Flag is opt-in, default is exclusion
- GIVEN a mixed bundle of public, private, and confidential concepts
- WHEN any of the six commands run without `--include-confidential`
- THEN confidential concepts are excluded

### Requirement: Exclusion, Not Redaction

The system MUST exclude confidential concepts from `llm.chat` payloads
entirely; it MUST NOT send a redacted, truncated, or masked version of a
confidential concept's content.

#### Scenario: No partial confidential content is sent
- GIVEN a confidential concept
- WHEN any of the six call sites builds its `llm.chat` payload without
  `--include-confidential`
- THEN none of that concept's content — full or partial — appears in the
  payload
