# Ingest Application Service Specification

## Purpose

The ingest bounded context's application service composes the
orchestration directly around the already-pure `extraction/concept.py`
leaf — de-presented derived-object staging and the plan-composition core
of the single-file ingest path — into callables usable by any adapter
without importing from `openkos.cli`. Workspace layout, configuration, an
`LLMBackend`, and decoded source text arrive as parameters, so the layer
binds no concrete backend and performs no filesystem I/O of its own. It is
the second artifact in the `application/` layer (ADR-0018), following the
shipped `application/query.py`.

## Non-Goals

Interactive confirmation and TTY detection; stdout/stderr rendering;
process exit-code selection; `_snapshot_read`, `guarded_targets`,
`_reject_drifted_targets`, `_autocommit`, `_refresh_derived_after_write`,
which the service calls through rather than owns; `_chat_client`/LLM
backend construction; the `Console(...).status` spinner and
`observability.phase_callback`; any change to `extract_concept`'s
contract, the on-disk format, or the CLI surface; `_ingest_batch`,
`_expand_batch_sources`, the batch cost gate; the `api`/`mcp` adapters
themselves; the headless-consent protocol.

## Requirements

### Requirement: Non-CLI Callable Ingest Composition

The service MUST expose a synchronous callable that composes derived-object
staging with the plan-composition core of the single-file ingest path,
importable and callable by code that imports nothing from `openkos.cli`.
It MUST receive its workspace layout, configuration, an `LLMBackend`, and
decoded source text as parameters rather than constructing or reading them
itself.

#### Scenario: A non-CLI caller stages derived objects

- GIVEN a module that imports nothing from `openkos.cli`
- WHEN it imports and calls the ingest application service with decoded
  source text, a workspace layout, a configuration, and an `LLMBackend`
- THEN it receives a result and no import of `openkos.cli` is triggered

#### Scenario: No concrete backend is bound inside the service

- GIVEN the ingest application service module
- WHEN its imports and call signatures are inspected
- THEN the `LLMBackend` arrives as a parameter and the module names no
  concrete backend implementation of its own

### Requirement: Extraction Disclosure Data Is Returned, Not Rendered

The service MUST return typed data for every notice, per-candidate drop,
and degrade condition it currently renders — the ordered set covered by
the existing `_<name>_notice(report)` helpers, per-candidate drop reasons
(empty slug, in-batch collision, on-disk exists, build failure), and
degrade reasons (`no-extractable-text`, `blocked-by-sensitivity`,
`failed`, including the caught `OllamaError`) — and MUST NOT call
`typer.echo` or any other presentation call to render them. The adapter
MUST render this data using the relocated `_notice` helpers, in the same
order and with the same wording as before this change.

#### Scenario: The service module renders nothing

- GIVEN the ingest application service module
- WHEN its source is inspected for calls to `typer`, `rich`, or
  `openkos.cli.observability`
- THEN none are found

#### Scenario: A degrade condition is returned as typed data

- GIVEN an extraction call that returns no extractable text
- WHEN the service composes the result
- THEN it returns a degrade reason distinguishing that case from
  `blocked-by-sensitivity` and `failed`, and prints nothing itself

### Requirement: Progress Reporting Is Injected, Never Owned

The service MUST accept an `on_progress` callable and forward it to the
extractor; it MUST NOT construct a `rich.Console` spinner or call
`observability.phase_callback` itself. The adapter builds both and passes
`on_progress` in.

#### Scenario: The adapter supplies progress reporting

- GIVEN a caller that passes an `on_progress` callback
- WHEN the service invokes the extractor
- THEN that callback is forwarded unchanged, and the service constructs no
  spinner or phase callback of its own

### Requirement: Decoded Text Arrives As A Parameter

The service MUST receive decoded source text (concept, index, and log
text) as parameters rather than performing any `_snapshot_read` itself;
the adapter performs every read that also feeds `guarded_targets`.

#### Scenario: The service reads no files

- GIVEN the ingest application service module
- WHEN its imports are inspected
- THEN it references no filesystem read primitive; text-dependent
  computations operate only on the parameters passed in

### Requirement: The #773 Convergence Short-Circuit Is A Typed Outcome

WHEN the plan-composition core reaches the byte-identical re-ingest
convergence case (issue #773), the service MUST return a typed outcome
distinguishing it from every other outcome, rather than terminating via an
internal early return. The adapter MUST map that outcome to the same
CLI-observable behavior (no model call, no write, exit 0, the disclosure
line naming `--re-extract`) as before this change.

#### Scenario: Convergence returns a typed outcome, not a raw return

- GIVEN a byte-identical re-ingest whose prior extraction ran to
  completion
- WHEN the service composes the plan
- THEN it returns a typed convergence outcome, and the adapter alone exits
  the command from that outcome

### Requirement: Shared Write Mechanics And Client Construction Stay Adapter-Side

The service MUST NOT hold a second definition of `_snapshot_read`,
`guarded_targets`, `_reject_drifted_targets`, `_autocommit`, or
`_refresh_derived_after_write`, and MUST NOT construct an LLM backend
(`_chat_client`/`OllamaClient`); the adapter constructs the backend and
calls the shared write helpers unchanged.

#### Scenario: Committing a plan uses the existing shared helpers

- GIVEN a plan produced by the service
- WHEN a caller commits it
- THEN the same shared write helpers used by every other write-capable
  command run, with no duplicate implementation inside the service

### Requirement: The Extraction Preserves Observable CLI Behavior

For every input covered by the existing `tests/unit/cli/test_ingest.py`
suite, `openkos ingest <file>` MUST produce the same exit code, stdout,
and stderr — including every output-text assertion — after this
extraction as before it.

#### Scenario: A previously-passing CLI scenario is unchanged

- GIVEN any scenario `test_ingest.py` covered before this change
- WHEN the same CLI invocation runs after the extraction
- THEN its exit code, stdout, and stderr are unchanged
