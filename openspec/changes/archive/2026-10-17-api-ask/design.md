# Design: Ask Through `openkos.api` And The Local API

Inputs: `proposal.md`, ADR-0058, ADR-0060, ADR-0061, ADR-0068 (Proposed). Paths
are under `src/openkos` unless they start with `tests/`, `docs/`, `desktop/` or
`openspec/`.

## Current state

- `application/query.py::run_query` composes the read-only store opens and
  `retrieval.answer.answer()`; it takes the chat and embedding clients, `limit`,
  `include_deprecated`, `include_confidential`, `local_exemption` and `progress`.
  The CLI's `query` and the MCP `query` tool are its two callers.
- Two implementations of one disclosure rule exist: `mcp/gate.py::disclose_query`
  for the MCP tool and `application/query_report.py::build_query_report` for
  `query --json`. Both start from `sensitivity.disclosable_concept_ids`, taken
  after the query ran, withhold every non-disclosable id from every channel,
  count it in `withheld`, and withhold the answer when its prompt held such a
  concept (`context_ids`) or a citation was withheld.
- `openkos.api` may import `openkos.application.*` but not `openkos.mcp` or
  `openkos.sensitivity` (the import guard). `openkos.localapi` may import only
  `openkos.api` among `openkos` modules.
- ADR-0058: the local API's responses are owner-local; "the sensitivity disclosure
  gate ... arrives with the first ask or query endpoint, which must apply it
  before any answer crosses the boundary".
- The judging run is a pollable job on its own daemon thread (`localapi/_runs.py`).

## Decisions

### D1. `ask` gates its own answer, through `query_report`

`ask` applies the rule itself, because `localapi` can reach nothing but
`openkos.api` and an ungated `ask` plus a separate gating call is one a client can
forget. The rule is reused from `application/query_report.py` (`disclosable_ids`
and `build_query_report`), the CLI's implementation of the MCP rule; the API may
not import `openkos.mcp.gate` and nothing is reimplemented. `Answer` is mapped
from the report's `outcome`, `answer`, `citations`, `omitted`, `attribution`,
`sufficiency` and `withheld`; nothing in `Answer` names an id the report did not
name. The citation's `confidential` and `history` markers, which the report does
not carry, are read from the same `Citation` the report kept (matched by id).
The package docstring's "applies no disclosure gate" statement gains the one
exception (ADR-0068).

### D2. One consent, `include_confidential`, as on the CLI

`include_confidential` means what `--include-confidential` means for `query
--json`: confidential concepts may enter retrieval (and so reach the configured
model, even when it is not verifiably local) and may be named in the answer.
Without it, the local exemption (`backends.resolve_local_exemption` on the built
chat client) still lets confidential concepts into a local model's prompt, and
the answer is then withheld. Two flags (model egress, result disclosure) were
considered and rejected: the CLI has one, and the combination "send to a remote
model but hide from the owner" has no user.

### D3. The API surface

```python
AskPhase = Literal["retrieving", "assembling", "checking", "synthesizing"]
AskOutcome = Literal["answered", "no_match", "no_answer_in_context", "withheld"]
NoMatchCause = Literal["none", "empty_query", "zero_hits", "all_unreadable",
                       "insufficient_context"]
AttributionStatus = Literal["reported", "none", "absent", "unparsed"]

@dataclass(frozen=True)
class AskProgress:
    phase: AskPhase
    completed: int
    total: int          # 4 with the sufficiency check, 3 without

@dataclass(frozen=True)
class AnswerCitation:
    concept_id: str
    title: str
    excerpted: bool     # the model saw an excerpt
    confidential: bool  # explicitly marked confidential (only with consent)
    history: Literal["superseded", "refined"] | None

@dataclass(frozen=True)
class OmittedConcept:
    concept_id: str
    title: str
    reason: Literal["context_budget", "context_budget_earlier_version"]

@dataclass(frozen=True)
class Answer:
    question: str
    outcome: AskOutcome
    answer: str | None              # None unless outcome == "answered"
    no_match_cause: NoMatchCause
    citations: tuple[AnswerCitation, ...]
    omitted: tuple[OmittedConcept, ...]
    attribution: AttributionStatus
    withheld: int                   # a count, never an id
    sufficiency_checked: bool
    sufficiency_degraded: bool
    lexical_unavailable: bool       # fts.db absent or unreadable
    dense_unavailable: bool         # vectors.db absent/unreadable, or the dense read degraded
    stale_indexes: tuple[str, ...]  # the stores `query` reads that predate the bundle

def ask(workspace, question, *, limit=8, include_deprecated=False,
        include_confidential=False,
        progress: Callable[[AskProgress], None] | None = None) -> Answer
```

`BackendEmbeddingDimensionMismatch`, which `run_query` can raise and which already
has the `OpenKosError` root, is exported. Everything is additive within
`API_VERSION` 1.

### D4. Failures

- `limit` below 1 (or a `bool`) raises `ValueError` before any client is built.
- An unreadable or invalid `openkos.yaml` raises `WorkspaceUnreadable`.
- `BackendUnavailable`, `BackendModelNotFound`, `BackendEmbeddingDimensionMismatch`,
  `BackendGenerationCapped` and `BackendError` propagate unchanged.
- An empty question is not an error: `no_match` with cause `empty_query`.
- A missing or unreadable derived store is not an error: it degrades and is
  reported by `lexical_unavailable` / `dense_unavailable`.
- An exception raised by `progress` propagates and ends the ask.

### D5. `ask` is read-only

It writes nothing, takes no lock and creates no derived store (`run_query`'s
existence-gated opens). The stale-index read is `status.stale_index_names(layout,
reads=("fts",))`, the CLI's.

### D6. The HTTP job

- `POST /v1/ask` with `{question, limit?, include_deprecated?, include_confidential?}`
  (`extra` forbidden, `limit >= 1` when given; omitted means the API default)
  returns 202 with the job and a `Location` header. A start while an ask is
  active returns 409 `ask_active` (retryable).
- The job is `{ask_id, state, phase, completed, total, answer, error}`: `state`
  is `running`, `finished` or `failed`; `phase`/`completed`/`total` are the last
  progress event (null before the first); `answer` is the `Answer` when finished;
  `error` is the fixed error body of the error table when the ask raised.
- `GET /v1/ask/{ask_id}` returns the job (200) for any known id, 404 `not_found`
  otherwise. The newest 16 finished or failed asks stay retrievable.
- The ask runs on its own daemon thread (registry `localapi/_asks.py`, framework-
  free), so a slow model holds no request thread and other routes stay served. An
  ask has no stop; shutdown abandons the daemon thread, which writes nothing.
- The ask registry is separate from the judging registry: an ask may run while
  a judging run is active.

### D7. The TypeScript client

`ask(body)` posts `/v1/ask`; `askJob(askId)` gets `/v1/ask/{ask_id}`. Types come
from the regenerated `schema.d.ts`. No polling helper: the screen owns its pace.

### D8. What `docs/` says

`docs/architecture.md`'s statement that the facade "applies no sensitivity
disclosure gate" gains the exception for an answer; the `openkos.localapi`
package docstring stops saying the gate is deferred. `docs/cli.md` is unchanged.

## Sequence: an ask over HTTP

1. `POST /v1/ask` → registry creates a job, starts a daemon thread, returns 202.
2. Thread: `api.ask` → read config → build clients → resolve exemption → stale
   indexes → `run_query` (progress updates the job) → disclosable set →
   `build_query_report` → `Answer`.
3. Job state `finished` with the `Answer`, or `failed` with the mapped error.
4. Client polls `GET /v1/ask/{id}` until the state is not `running`.

## Testing

- API: a public and a confidential concept matching one question, an FTS index,
  fake chat/embedding clients patched at `_wiring`'s client classes (no real
  model). Cases: answered with citations; confidential never in the prompt to a
  remote model without consent; local exemption puts it in the prompt and the
  answer is withheld, counted, never named; consent names it with
  `confidential: true`; empty question; zero hits; sufficiency refusal; limit
  validation; unreadable config; backend errors propagate; progress phases;
  read-only snapshot; stale index reported.
- Surface: regenerated snapshot, classifier prints additive lines only.
- HTTP: start 202 + poll to finished; `ask_active`; unknown id 404; a raising
  ask ends `failed` with a fixed body; invalid body 422; the answer is the
  gated one; OpenAPI snapshot regenerated.
- Client: method tests for path, method and body; `check:schema`.
