# Delta for Query Application Service

## MODIFIED Requirements

### Requirement: Non-CLI Callable Answer Composition

The service MUST expose a synchronous callable that composes index opening
with degrade handling and the `answer()` call, importable and callable by
code that imports nothing from `openkos.cli`.

It MUST receive its workspace layout, configuration, LLM backend and
embedder as parameters rather than constructing them, so that no concrete
backend is bound inside the application layer and every adapter supplies
its own. Workspace gating stays the caller's step: `config.require_workspace`
already returns a refusal reason rather than printing or exiting, so an
adapter can refuse an uninitialized workspace in its own idiom.

The callable (`run_query`) MUST additionally accept an optional keyword-only
`progress` callback and thread it, unmodified, into its `answer()` call.
WHEN a caller omits `progress` (the default), the composed call's behavior,
return value, and every side effect MUST remain byte-identical to the
callable's contract before this parameter existed.
(Previously: `run_query` had no `progress` parameter and could not thread
one into `answer()`.)

#### Scenario: A non-CLI caller answers a question

- GIVEN a module that imports nothing from `openkos.cli`
- WHEN it imports and calls the query application service with a question,
  a workspace layout, a configuration, an LLM backend and an embedder
- THEN it receives a result and no import of `openkos.cli` is triggered

#### Scenario: No concrete backend is bound inside the service

- GIVEN the query application service module
- WHEN its imports and call signature are inspected
- THEN the LLM backend and embedder arrive as parameters, and the module
  names no concrete backend implementation of its own

#### Scenario: Progress is threaded through unmodified

- GIVEN a caller supplies a `progress` callback to the composed callable
- WHEN it calls `run_query(..., progress=callback)`
- THEN the same `callback` object is passed through to the underlying
  `answer()` call without wrapping or modification

#### Scenario: Omitting progress keeps composition byte-identical

- GIVEN a caller invokes the composed callable without a `progress`
  argument
- WHEN it runs
- THEN its return value and its call to `answer()` are byte-identical to
  the callable's behavior before the `progress` parameter existed
