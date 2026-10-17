# Delta for stable-python-api

## RENAMED Requirements

- FROM: `### Requirement: The Surface Is Owner-Local And Applies No Disclosure Gate`
- TO: `### Requirement: The Surface Is Owner-Local And Gates Only An Answer`

## MODIFIED Requirements

### Requirement: The Surface Is Owner-Local And Gates Only An Answer

The Python API MUST NOT apply the sensitivity disclosure gate to any result
other than the `Answer` that `ask` returns, and its documentation (the package
docstring) MUST state that it is an owner-local, in-process surface whose
caller is the owner, that `ask` applies the query disclosure rule to its answer
(ADR-0068), and that any other disclosing adapter MUST apply the local API's
gate itself (ADR-0058). Results other than an answer MAY therefore carry stored
content. The API MUST NOT send content to any model backend other than the
configured one, and only through the judging run and `ask`. The only other
process it invokes is the local git auto-commit for a decision it applies,
which never pushes. The docstring MUST also state that cancellation is
cooperative (a stop waits for the chunk in flight), that v1 apply does not
reconcile merged bodies and does not refresh the derived stores, and that
`API_VERSION`, not the package version, is the compatibility promise.

(Previously: the API applied no disclosure gate to any result and sent content
to a model only through the judging run.)

#### Scenario: The docstring states the boundary

- GIVEN `openkos.api` is imported
- WHEN its `__doc__` is read
- THEN it states that the surface is owner-local and that only `ask` applies a
  disclosure rule, to its answer

#### Scenario: The facade does not depend on the gate

- GIVEN the import guard
- WHEN it inspects `openkos.api`
- THEN no module imports `openkos.mcp.gate`

## ADDED Requirements

### Requirement: A Question Is Asked Under The Query Disclosure Rule

`ask(workspace, question, *, limit=8, include_deprecated=False,
include_confidential=False, progress=None)` MUST answer `question` through the
query application service the CLI's `query` uses, with the chat and embedding
clients built from the configuration read afresh by the API's own composition
root, and the local exemption resolved by the shared resolver on the built chat
client. It MUST write nothing, take no lock and create no derived store.

It MUST return an `Answer` whose `outcome` is `answered`, `no_match` (every
no-match cause except the sufficiency refusal), `no_answer_in_context` (the
sufficiency refusal) or `withheld`, built by the same rule `query --json` and
the MCP `query` tool apply, through `application.query_report`, with the
disclosable set taken after the query ran:

- Without `include_confidential`, no confidential concept is named, titled or
  quoted anywhere in the `Answer`; it is counted in `withheld` only.
- An answer whose prompt held a concept outside the disclosable set, or one of
  whose citations was withheld, has `outcome` `withheld` and `answer` `None`.
- `answer` is the text only when `outcome` is `answered`.

Without `include_confidential`, a confidential concept MUST reach the chat
model only when the shared resolver grants the local exemption (a verifiably
local host and the workspace's exemption on). With it, confidential concepts
enter retrieval and MAY be named, each citation marking `confidential`.
`include_deprecated` includes deprecated and superseded concepts as on the CLI.

`limit` below 1, or a `bool`, MUST raise `ValueError` before any client is
built. An unreadable or invalid configuration MUST raise `WorkspaceUnreadable`.
The neutral backend errors (`BackendUnavailable`, `BackendModelNotFound`,
`BackendEmbeddingDimensionMismatch`, `BackendGenerationCapped`, `BackendError`)
MUST propagate unchanged. `progress`, when given, MUST be called with an
`AskProgress(phase, completed, total)` for each answer phase in order.

The `Answer` MUST also report the no-match cause, the documents the context
budget left out (`omitted`, filtered like citations), the attribution status,
whether the sufficiency check ran and whether it could not run, whether the
lexical or dense retriever was unavailable, and the stale derived stores the
query reads.

#### Scenario: A grounded answer cites its concepts

- GIVEN a workspace whose index matches a public concept and a model that
  answers citing it
- WHEN `ask` is called
- THEN `outcome` is `answered`, `answer` is the text and the citation names the
  concept, and the workspace bytes are unchanged

#### Scenario: A remote model never receives a confidential concept without consent

- GIVEN a confidential and a public concept matching the question and a chat
  backend that is not local
- WHEN `ask` is called without `include_confidential`
- THEN the confidential body is absent from every prompt sent and the
  confidential concept is counted in `withheld`, never named

#### Scenario: A local model's answer over a confidential concept is withheld

- GIVEN the same concepts and a local chat backend with the exemption on
- WHEN `ask` is called without `include_confidential`
- THEN the confidential body reaches the prompt, `outcome` is `withheld`,
  `answer` is `None`, and no field names the confidential concept

#### Scenario: Consent names a confidential citation

- GIVEN the same concepts
- WHEN `ask` is called with `include_confidential=True`
- THEN the answer is returned and the confidential concept's citation has
  `confidential` true

#### Scenario: An empty question is a no-match, not an error

- GIVEN any workspace
- WHEN `ask` is called with a blank question
- THEN `outcome` is `no_match` with cause `empty_query` and no model is called

#### Scenario: A sufficiency refusal is its own outcome

- GIVEN the sufficiency check is on and the model replies that the context
  does not answer
- WHEN `ask` is called
- THEN `outcome` is `no_answer_in_context` and `answer` is `None`

#### Scenario: An invalid limit is refused before any client

- GIVEN a factory that fails the test when a client is built
- WHEN `ask` is called with `limit=0`
- THEN `ValueError` is raised

#### Scenario: An unreachable backend is the neutral error

- GIVEN a chat backend that cannot be reached
- WHEN `ask` is called
- THEN `BackendUnavailable` is raised

#### Scenario: Progress reports the phases in order

- GIVEN the sufficiency check is on
- WHEN `ask` is called with a progress callback
- THEN it hears `retrieving`, `assembling`, `checking` and `synthesizing` with
  a total of 4

### Requirement: Ask Extends v1 Additively

`ask`, `Answer`, `AnswerCitation`, `OmittedConcept`, `AskProgress` and
`BackendEmbeddingDimensionMismatch` MUST be exported, and the committed surface
snapshot MUST be regenerated so the classifier reports only additive changes;
`API_VERSION` MUST stay 1.

#### Scenario: The snapshot change is additive

- GIVEN the snapshot from `main` before this change
- WHEN the classifier compares it with the live surface
- THEN every change it prints is additive and `API_VERSION` is 1
