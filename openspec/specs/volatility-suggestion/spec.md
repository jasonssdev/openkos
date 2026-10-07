# Volatility Suggestion Specification

## Purpose

`volatility-suggestion` covers freshness suggestion: a read-only CLI
verb, `suggest-volatility`, that asks the LLM to propose a volatility tier
(`static`/`slow`/`volatile`) plus a rationale for each concept type present
in the workspace, and points the human at `set-volatility` to apply an
accepted suggestion. The verb itself performs zero writes.

## Non-Goals

This spec does NOT define: config writes or auto-accept of a suggestion
(`set-volatility` owns the write — `volatility-config` — and a human still
runs it; this verb applies nothing on their behalf); duration/window value
suggestions; contradiction or staleness detection (S3); a guided reconcile
write-verb (S4). This extends ADR-0007; no new ADR.

## Requirements

### Requirement: Workspace-Gated, Read-Only Per-Type Suggestion

The system MUST provide a CLI verb, `suggest-volatility`, that requires an
active workspace and, for every concept type present in the bundle, prints
an LLM-suggested tier (one of `static`, `slow`, `volatile`) and a rationale.
The verb MUST perform ZERO writes to any bundle file, index, log, or config.
Output MUST be a plain stdout report ending with a hint to run `openkos
set-volatility <ConceptType> <tier>` to apply an accepted suggestion.

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

### Requirement: Ordered BackendError Handling

The system MUST handle `BackendError` in the same 3-tier order used by
`suggest-relations`: `BackendUnavailable` first, then `BackendModelNotFound`,
then the generic `BackendError` branch. Each branch MUST print a clear
message to stderr and exit non-zero, with zero writes performed.

#### Scenario: Ollama unreachable

- GIVEN the LLM backend raises `BackendUnavailable`
- WHEN `suggest-volatility` runs
- THEN stderr states the configured backend is not responding and how to
  start it (`ollama serve` for `ollama`, the endpoint host for
  `openai-compatible`), and the process exits non-zero with no writes

#### Scenario: Model not found

- GIVEN the LLM backend raises `BackendModelNotFound`
- WHEN `suggest-volatility` runs
- THEN stderr states the model is missing with a pull remedy and the
  process exits non-zero with no writes

#### Scenario: Generic Ollama error

- GIVEN the LLM backend raises a generic `BackendError`
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

### Requirement: A Suggestion Is Judged Against The Effective Tier

A type's current tier MUST be its EFFECTIVE tier: the workspace's `type_tiers`
entry when it names a valid tier, otherwise the registry default. The prompt
MUST name that tier, and a suggestion equal to it MUST NOT be proposed -- no
pending-work row is enqueued for it and `curate` does not offer it -- because a
tier the user already applied is settled.

#### Scenario: An applied tier is not proposed again

- GIVEN `Event` defaults to `static` and `type_tiers` maps `Event` to `slow`
- WHEN the model suggests `slow` for `Event`
- THEN no `volatility` pending-work row exists for `Event`
- AND `curate` offers no tier change for `Event`

### Requirement: Answered Questions Are Cached, Not Queued

When run by the daemon or by `curate`, every non-degraded answer MUST be kept in
`.openkos/findings.db`, keyed on the exact prompt sent and the model, so an
unchanged type is not asked again. Only a suggestion that differs from the
effective tier becomes a pending-work row; a "keep the current tier" answer is
cached and never queued, because it names nothing for a person to act on. A
degraded answer MUST NOT be cached. A change to the sampled bodies, the
effective tier, the rationale language or the model MUST re-ask the type. The
standalone `suggest-volatility` verb remains read-only and does not read or
write the cache. `forget` MUST remove cached answers whose prompt carried a
forgotten concept.

#### Scenario: An unchanged bundle costs no model call

- GIVEN a maintenance pass has already answered every type
- WHEN the next pass runs over an unchanged bundle
- THEN the model is asked nothing

#### Scenario: Changing type_tiers re-asks the type

- GIVEN a type's answer is cached
- WHEN `type_tiers` changes that type's effective tier
- THEN the type is asked again
