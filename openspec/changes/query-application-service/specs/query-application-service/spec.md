# Query Application Service Specification

## Purpose

The query bounded context's application service composes the orchestration
around `retrieval.answer()` — config and workspace resolution, LLM and
embedder construction, index opening with degrade handling — and the
`--save` filing domain logic (title derivation, sensitivity high-water-mark,
duplicate-question disclosure) into callables usable by any adapter without
importing from `openkos.cli`. It is the first artifact in an `application/`
layer for bounded-context composition sitting above both the canonical and
derived layers.

## Non-Goals

Interactive confirmation and TTY detection; stdout/stderr rendering; process
exit-code selection; the shared write mechanics (`_reject_drifted_targets`,
`_autocommit`, `_refresh_derived_after_write`), which the service calls
through rather than owns; any change to `answer()`'s contract, the on-disk
format, or the CLI surface; the `api`/`mcp` adapters themselves; ingest and
lifecycle composition.

## Requirements

### Requirement: Non-CLI Callable Answer Composition

The service MUST expose a callable that composes workspace/config
resolution, LLM/embedder construction, index opening with degrade handling,
and the `answer()` call, importable and callable by code that imports
nothing from `openkos.cli`. It MUST report a not-an-initialized-workspace
condition through its return contract rather than by printing or exiting
the process.

#### Scenario: A non-CLI caller answers a question

- GIVEN a module that imports nothing from `openkos.cli`
- WHEN it imports and calls the query application service with a question
  and a workspace root
- THEN it receives a result and no import of `openkos.cli` is triggered

#### Scenario: An uninitialized workspace is reported, not rendered

- GIVEN a workspace root that is not initialized
- WHEN the service is called
- THEN it reports that condition through its return contract, without
  writing to stdout/stderr or calling `sys.exit`

### Requirement: Filing Composition Is Independently Callable

The service MUST expose a callable that composes the `--save` filing domain
logic (title-derivation cascade, sensitivity high-water-mark, duplicate-
question disclosure lookup) from an `AnswerResult`/citation set into a
plan, independent of any CLI confirmation step, without performing the
write.

#### Scenario: A filing plan is computed without writing

- GIVEN an `AnswerResult` with citations and a workspace root
- WHEN the filing composition is called
- THEN it returns a plan (title, sensitivity, provenance, duplicate
  disclosures) and the bundle stays unchanged until a caller commits it

#### Scenario: Zero citations refuse at the service boundary

- GIVEN an `AnswerResult` with empty citations
- WHEN the filing composition is called
- THEN it refuses to produce a plan and reports that refusal through its
  return contract

### Requirement: Shared Write Mechanics Are Called Through, Never Forked

The service MUST commit a filing plan by calling the existing shared write
helpers (`_reject_drifted_targets`, `_autocommit`, `_refresh_derived_after_write`,
and equivalents) unchanged, and MUST NOT hold a second definition of any of
them.

#### Scenario: Committing a plan uses the existing shared helpers

- GIVEN a filing plan produced by the service
- WHEN a caller commits it
- THEN the same shared write helpers used by every other write-capable
  command run, with no duplicate implementation inside the service

### Requirement: Adapter Owns Interaction, Presentation, And Exit Codes

The service MUST NOT perform interactive confirmation, TTY detection,
stdout/stderr rendering, or process exit-code selection; those stay with
the calling adapter.

#### Scenario: The CLI still owns the confirmation gate

- GIVEN `--save` invoked from the CLI without `--auto` on a TTY
- WHEN the query command runs
- THEN the confirmation prompt and TTY detection happen in the CLI
  adapter, not inside the service

### Requirement: The Extraction Preserves Observable CLI Behavior

For every input covered by the existing `test_query.py`/`test_query_save.py`
CLI tests, `openkos query` and `openkos query --save` invoked through the
CLI MUST produce the same exit code, stdout, and stderr after this
extraction as before it.

#### Scenario: A previously-passing CLI scenario is unchanged

- GIVEN any scenario the CLI test suite covered before this change
- WHEN the same CLI invocation runs after the extraction
- THEN its exit code, stdout, and stderr are unchanged
