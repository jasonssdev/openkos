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

### Requirement: Walk-Incompleteness Observability

The system MUST detect when the directory walk underlying the fail-closed
sensitivity filter is provably incomplete (`okf._walk_errors` reports one or
more unlistable subdirectories) and MUST emit a warning to STDERR identifying
the incomplete-walk condition, for each of the five sensitivity-filter verbs:
`query`, `contradictions`, `adjudicate`, `suggest-relations`,
`suggest-volatility`. The command MUST still exit 0 (WARN, not refuse). The
warning MUST be skipped when `--include-confidential` is passed, since the
filter is then deliberately disabled. A future cloud-egress mode that instead
REFUSES on this condition is explicitly out of scope for this change.

#### Scenario: Incomplete walk warns and still exits 0
- GIVEN a bundle where `okf._walk_errors` reports at least one unlistable
  subdirectory
- WHEN `query`, `contradictions`, `adjudicate`, `suggest-relations`, or
  `suggest-volatility` runs without `--include-confidential`
- THEN the command prints a warning to STDERR identifying the incomplete walk
- AND exits 0

#### Scenario: Clean bundle produces no warning
- GIVEN a bundle where `okf._walk_errors` reports no unlistable subdirectories
- WHEN any of the five verbs runs
- THEN no incomplete-walk warning is printed to STDERR

#### Scenario: `--include-confidential` suppresses the warning
- GIVEN a bundle where `okf._walk_errors` reports an unlistable subdirectory
- WHEN any of the five verbs runs WITH `--include-confidential`
- THEN no incomplete-walk warning is printed, since the filter is
  deliberately off

### Requirement: Defense-in-Depth Sensitivity Re-Check at Load

Each of `contradictions`, `adjudicate`, `suggest-relations`, and
`suggest-volatility` MUST apply an independent fail-closed re-check — via
`sensitivity.blocks_llm_send` against that document's own frontmatter — at
the point a candidate/member/pair document is loaded by direct path, before
its content enters the `llm.chat` payload. This re-check MUST NOT depend on
whether the document was present in the precomputed blocked set built during
the directory walk: a confidential document absent from that set (e.g.
because its subtree became unlistable, or a permission change occurred,
after the walk but before the load) MUST still be excluded.
`--include-confidential` MUST bypass this re-check identically to how it
bypasses walk-based exclusion, restoring byte-identical pre-filter behavior.
`query` already implements this re-check (S3 FIX-2, answer.py:211-214) and
requires no behavior change.

#### Scenario: Confidential doc absent from the precomputed blocked set is caught at load
- GIVEN a confidential document that was NOT added to the precomputed
  blocked set (its containing subtree lost read permission after indexing,
  but the doc is still reachable and loaded by direct path)
- WHEN `contradictions`, `adjudicate`, `suggest-relations`, or
  `suggest-volatility` loads that document without `--include-confidential`
- THEN the independent per-doc re-check excludes it before it enters the
  `llm.chat` payload

#### Scenario: `--include-confidential` bypasses the re-check
- GIVEN the same confidential document as above
- WHEN any of the four verbs runs WITH `--include-confidential`
- THEN the document is loaded and sent exactly as pre-filter behavior would

#### Scenario: Query is already conformant
- GIVEN `query`'s existing send-time `sensitivity.blocks_llm_send` re-check
  (S3 FIX-2, answer.py:211-214)
- WHEN this change ships
- THEN `query`'s behavior is unchanged — it already independently re-checks
  each candidate at load, satisfying this requirement without modification
