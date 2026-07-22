# Volatility Suggestion Specification

## Purpose

`volatility-suggestion` is slice 2 of the freshness work: a read-only CLI
verb, `suggest-volatility`, that asks the LLM to propose a volatility tier
(`static`/`slow`/`volatile`) plus a rationale for each concept type present
in the workspace, and points the human at the `type_tiers:` config layer to
apply an accepted suggestion by hand-edit. Zero writes.

## Non-Goals

This spec does NOT define: config writes or auto-accept of a suggestion (no
safe partial-YAML writer exists — hand-edit only); duration/window value
suggestions; contradiction or staleness detection (S3); a guided reconcile
write-verb (S4). This extends ADR-0007; no new ADR.

## Requirements

### Requirement: Workspace-Gated, Read-Only Per-Type Suggestion

The system MUST provide a CLI verb, `suggest-volatility`, that requires an
active workspace and, for every concept type present in the bundle, prints
an LLM-suggested tier (one of `static`, `slow`, `volatile`) and a rationale.
The verb MUST perform ZERO writes to any bundle file, index, log, or config.
Output MUST be a plain stdout report ending with a hint to hand-edit
`type_tiers:` in `openkos.yaml`.

#### Scenario: Verb suggests a tier per type

- GIVEN a bundle containing `Person` and `Procedure` concepts
- WHEN `suggest-volatility` runs inside the workspace
- THEN it prints one suggested tier and rationale for each type present
- AND the report ends with a hint to edit `type_tiers:` in `openkos.yaml`

#### Scenario: Verb requires an active workspace

- GIVEN no workspace is active
- WHEN `suggest-volatility` runs
- THEN it fails with the standard `require_workspace` gate error, before any
  LLM call

#### Scenario: Verb performs zero writes

- GIVEN a bundle with multiple concept types
- WHEN `suggest-volatility` runs to completion
- THEN no bundle file, index, log, or `openkos.yaml` is modified on disk

### Requirement: Fail-Closed Per-Type Suggestion Parsing

The system MUST parse each type's LLM output fail-closed. WHEN a type's
response is missing, malformed, or names a value outside `{static, slow,
volatile}`, that type's entry MUST degrade to a `[?]` marker with a note,
and MUST NOT abort or crash the run for the remaining types.

#### Scenario: One malformed type degrades, run continues

- GIVEN the LLM returns unparseable output for one of three concept types
- WHEN `suggest-volatility` runs
- THEN the two well-formed types print normal suggestions, the malformed
  type prints a `[?]` entry with a note, and the run exits successfully

#### Scenario: Invalid tier value is not surfaced as valid

- GIVEN the LLM suggests a tier not in `{static, slow, volatile}` for a type
- WHEN `suggest-volatility` runs
- THEN that type's entry is printed as `[?]`, never as an accepted tier

### Requirement: Ordered OllamaError Handling

The system MUST handle `OllamaError` in the same 3-tier order used by
`suggest-relations`: `OllamaUnavailable` first, then `OllamaModelNotFound`,
then the generic `OllamaError` branch. Each branch MUST print a clear
message to stderr and exit non-zero, with zero writes performed.

#### Scenario: Ollama unreachable

- GIVEN the LLM backend raises `OllamaUnavailable`
- WHEN `suggest-volatility` runs
- THEN stderr states Ollama is not responding and the process exits
  non-zero with no writes

#### Scenario: Model not found

- GIVEN the LLM backend raises `OllamaModelNotFound`
- WHEN `suggest-volatility` runs
- THEN stderr states the model is missing with a pull remedy and the
  process exits non-zero with no writes

#### Scenario: Generic Ollama error

- GIVEN the LLM backend raises a generic `OllamaError`
- WHEN `suggest-volatility` runs
- THEN stderr states a generic failure message and the process exits
  non-zero with no writes

### Requirement: Deterministic Input Selection

For a fixed bundle, the set and order of concept bodies sampled per type
and shown to the LLM MUST be deterministic across repeated runs. This
requirement covers the determinism of the INPUT selection only; the LLM's
own textual output is not required to be deterministic.

#### Scenario: Same bundle yields same sampled input

- GIVEN the same bundle is used for two separate `suggest-volatility` runs
- WHEN the per-type concept bodies are selected and passed to the LLM
- THEN the set and order of sampled bodies is identical across both runs
