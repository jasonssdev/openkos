# Proposal: mcp-read-surface — a read-only MCP server that never discloses what it must not

## Intent

Closes #1009 and #1010. The exploration is `exploration.md` in this folder, and
its "Decisions (2026-09-26)" section is binding.

MVP 3's stated outcome is that a user can "ask their knowledge base questions
from a chat client they already have, see what the base is waiting on" without a
terminal (`docs/roadmap.md`, MVP 3). The application services that make this
possible now exist: `run_query`, `list_bundle_objects`/`list_provenance_sources`,
`next_action`, and the graph projection. What is missing is the adapter, which
is MVP 3's last deliverable.

The adapter creates a new egress boundary. Every existing sensitivity gate
protects **LLM egress** (`sensitivity.should_block`, `blocks_llm_send`). None
of them protects **disclosure to another program**. A chat client that receives
a tool result usually forwards it to a remote model provider, which the server
cannot observe. The exploration confirmed that disclosure leaks exist today:
`application/next_action.py` imports nothing from `sensitivity`, and its reasons
interpolate `f"{finding.concept_id}: {finding.detail}"` (`:594`, `:643`, `:679`).
The graph is sensitivity-blind by construction, and `ProvenanceSources.rows` is
the whole bundle. #1010 is therefore a hard prerequisite, and it ships as this
change's first slice.

Success means that an MCP client over stdio can call `query`, `get`, `navigate`,
and `pending` against one workspace. By default, no response on any path,
including error paths, carries a confidential object's id, title, or content.
Every withheld object is reported as a count. A read that may have observed an
in-flight write says so. The CLI's behavior does not change.

## Scope

### In Scope

1. **The disclosure predicate (#1010)** in `sensitivity.py`, which stays a pure
   leaf. It uses a per-value check and a set-producing sibling for bulk rows. It
   applies the **strict fail-closed rank** of `blocks_llm_send`: absent, blank,
   unrecognized, unreadable, or unparseable all count as confidential. It takes
   one policy input (whether the launch opt-in is on) and **no**
   `include_confidential` or `local_exemption` parameter.
2. **The MCP server package `src/openkos/mcp/`.** It is hand-rolled,
   tools-only, and uses stdio with newline-delimited JSON-RPC 2.0. It targets
   protocol revision **2025-11-25** only. It adds **no new dependency**. It
   supports:
   - `initialize` and `notifications/initialized`, declaring the `tools`
     capability with `listChanged: false`;
   - `ping`, which that revision makes a MUST for the receiver;
   - `tools/list` and `tools/call`;
   - `notifications/progress` (outbound) and `notifications/cancelled`
     (inbound).
3. **Four tools, all read-only and lock-free** (ADR-0020 D3):
   - `query(question, limit?)` calls `run_query`.
   - `get(concept_id)` returns one concept's curated fields, its body, its
     filtered relations and provenance, and its source ancestors via
     `list_provenance_sources`, including `not_run`.
   - `navigate(concept_id)` returns the concept's neighbors (typed relations
     plus untyped links) from `build_graph`.
   - `pending()` calls `next_action`.
4. **A disclosure gate module** (`mcp/gate.py`). It is the only place the MCP
   surface calls the predicate. It filters rows, edges, citations, and title
   lists, and adds a `withheld` count to every result. The services stay
   complete, and the gate never mutates a shared service result in place.
5. **An enumeration guard** over the tool registry. Every registered tool,
   including its error paths, runs against a fixture holding a confidential
   canary object, and the guard asserts the canary's id, title, and body
   marker appear in no serialized response. A deliberately violating test-only
   tool is written first, and the guard must be observed catching it.
6. **Consistency warnings.** Each response carries `warnings`, a list:
   - `in_flight_write` (count only), from `bundle_ledger.scan_torn_writes`,
     on every tool;
   - `stale_index` (the names of the derived stores), from
     `state.derived.stale_derived_stores`, on `query` only.

   A check that cannot run becomes a `not_run` entry, never a raise (ADR-0022).
7. **`pending`'s leak fixed at its source.** `NextAction` and each declination
   gain structured subject ids, as an additive field. The CLI output stays
   byte-identical. The gate withholds any item whose subjects are not all
   disclosable. Skip notices are reported only as a count.
8. **Query progress** (ADR-0021 D7). `answer()` and `run_query` gain an
   optional `progress` callback, which defaults to `None` and is byte-identical
   when absent. The adapter translates it into `notifications/progress` when
   the request carries a `progressToken`.
9. **Chat-client construction moved out of the CLI.** `_chat_client` and
   `_resolve_local_exemption` move from `cli/main.py` into
   `application/backends.py`. This is a behavior-preserving move, so the MCP
   adapter can build a backend without importing `openkos.cli`.
10. **The CLI verb `openkos mcp`**, which serves over stdio. It is in the
    `Explore` help panel and takes `--workspace DIR` and
    `--expose-confidential`.
11. **Specs:**
    - the new `mcp` domain;
    - deltas to `sensitivity-aware-llm`, `next-action-pointer`,
      `query-answer`, and `query-application-service`.
12. **ADRs:**
    - ADR-0027 (hand-rolled stdio server, no SDK, revision target);
    - ADR-0028 (MCP disclosure as a boundary separate from LLM egress), which
      design confirms under the ADR gate;
    - ADR-0020 and ADR-0021 become Accepted at archive.
13. **Docs:**
    - an `mcp` entry in `docs/cli.md`, including a client configuration
      example;
    - the `mcp/` package in `docs/architecture.md`;
    - MVP 3 status in `docs/roadmap.md`.

### Out of Scope

- Write tools of any kind. Therefore no lock acquisition, no
  `WorkspaceBusyError` handling, and no retry or backoff are needed (ADR-0020
  D5 applies to the first write tool, not here).
- Protocol revision 2026-07-28 (stateless), and choosing the revision by
  inspecting the first message. These are a follow-up.
- Resources, prompts, sampling, elicitation, roots, completion, the `logging`
  capability, `listChanged`, and `tools/list` pagination.
- HTTP or streamable transports, a REST API, and authentication.
- A per-request confidential opt-in, and any MCP meaning for
  `local_exemption`.
- Redacting prose. A disclosable object's body is returned as written (see
  Product decision P1).
- Caching the graph projection across calls: `build_graph` rebuilds per
  `navigate` call. This is measured before any optimisation.
- A bound on abandoned worker threads (ADR-0021 "Accepted risk").
- Correcting the lock's same-process reentrance message (ADR-0021 D5
  follow-on).
- Detecting an interrupted `index.md` rewrite. `scan_torn_writes` covers only
  the merge ledger's two-phase marker (ADR-0020 "Harder").
- `examples/good-life-demo/` changes.

## Decisions

| Decision | Chosen | Why (one line) |
| --- | --- | --- |
| SDK | Hand-rolled, no new dependency | Binding. The official `mcp` 2.2.0 pulls 28 distributions including `pydantic`, which `AGENTS.md` excludes, and costs about 250 ms to import. |
| Protocol revision | 2025-11-25 only. An `initialize` naming another version is answered with 2025-11-25, and the client decides whether to continue | Binding. This is what deployed clients speak, and the spec defines this negotiation. |
| JSON-RPC batches | Rejected with `-32600` | Batching is absent from the targeted revision. Accepting it would be unspecified behavior. |
| `ping` | Supported | The targeted revision says the receiver MUST respond. It costs a few lines. |
| Verb shape | `openkos mcp`, which serves stdio directly, with no `serve` subcommand | There is one transport. A future transport becomes an option on the same verb, not a new subtree. |
| Help panel | `Explore` (an existing entry in `PANEL_ORDER`) | MVP 3 is "the ask surface". One verb does not justify a new panel. |
| Workspace selection | `--workspace DIR`, defaulting to the current directory, validated before serving (the server exits nonzero, with a message on stderr, if the directory is not a workspace) | Common MCP clients launch servers without a configurable cwd. One process serves one workspace (ADR-0021). |
| Confidential opt-in | `--expose-confidential`, read at launch only, never per request, and off by default | Binding. It is deliberately not named `--include-confidential`, which on `query` means "send to the LLM". This flag governs disclosure to the client, which is a different boundary. |
| "Verifiably local client" | Has no meaning, so there is no exemption | A stdio client is always a local process, but it forwards tool results to a model provider the server cannot observe. Locality of the peer says nothing about egress. |
| `query`'s LLM egress | With the flag off: `run_query(include_confidential=False, local_exemption=False)`. With the flag on: `local_exemption` is resolved exactly as the CLI resolves it, and `include_confidential` is always `False` | The answer text itself goes to the client. With the flag off, confidential content never enters the prompt, so the answer is clean by construction. With the flag on, a confidential object still needs **both** gates, and a remote chat backend never receives one. |
| Where the predicate lives | `sensitivity.py`, next to `blocks_llm_send` and reusing its rank | Binding. It keeps one fail-closed authority, and the leaf stays pure. |
| Where it is applied | Only in `mcp/gate.py`, at the adapter boundary | Binding. The services stay complete and the CLI is unchanged. There is one call site to audit. |
| Withheld reporting | Every result carries `withheld: <int>`. A `get` or `navigate` on a withheld id returns an empty object with `withheld: 1` | Binding: count, never content. Naming an id the caller already supplied discloses only that the id exists. |
| `get` output | A curated field set (id, type, title, sensitivity, status, body, filtered relations/provenance/ancestors, `not_run`), never a frontmatter passthrough | It fails closed against frontmatter keys added later that might carry ids. |
| `pending` gating | Structured subject ids are added to `NextAction` and to declinations. The gate withholds an item unless all its subjects are disclosable, and a tier with no declared subjects is withheld | Matching on free text cannot be made fail-closed, because lint's `detail` is free prose. Structure keeps the rule mechanical. |
| Skip notices over MCP | A count only | They name unreadable documents, which the predicate ranks as confidential. |
| Title lists in `AnswerResult` | `omitted`/`excerpted`/`history_truncated` titles for withheld ids are scrubbed and counted | #882's lists do not say why a title was held back. Binding. |
| Graph edges | An edge survives only if both ends are disclosable. The adapter filters it | Binding. The graph is sensitivity-blind by construction. |
| Consistency field | `warnings: [{code, count \| stores, message}]`, where `in_flight_write` is a count only | Binding (answer plus a warning). A survivor id would itself be a disclosure channel. |
| Module layout | `mcp/transport.py` (framing), `mcp/server.py` (lifecycle, dispatch, cancellation, progress), `mcp/tools.py` (registry and hand-written schemas), `mcp/gate.py` (disclosure) | Each file has one reason to change, and the gate is auditable alone. |
| Reading stdin | A dedicated reader thread feeds an `asyncio.Queue`. Writes go to `sys.stdout.buffer` from the loop thread | This is portable: pipe-reading on Windows's Proactor loop is unreliable for console and stdio handles. Binary writes avoid `\r\n` translation. |
| Stdout hygiene | The transport keeps a private handle to the real stdout, and `sys.stdout` is rebound to stderr while serving. Logs use stdlib `logging` to stderr only | A stray `print` anywhere below would corrupt the protocol stream. This makes that structurally impossible. |
| Concurrency | One asyncio task per request, and one `asyncio.to_thread` worker per tool call | ADR-0021 D2. Read tools take no lock (ADR-0020 D3). |
| Cancellation | `notifications/cancelled` cancels the awaiting task. No response is sent. The worker is abandoned, not stopped, and this is logged on stderr. `initialize` is not cancellable | ADR-0021 D3. For read tools, an abandoned worker writes nothing. |
| Progress | Only `query` emits progress, and only when `_meta.progressToken` is present. Phase events are marshalled with `loop.call_soon_threadsafe`, with a known total and monotonic progress | ADR-0021 D7. The other tools are fast. |
| Timeouts | None added | ADR-0021 D8. |
| Protocol errors | `-32700` parse, `-32600` invalid request (including before `initialize` and batches), `-32601` unknown method, `-32602` invalid params or unknown tool, `-32603` internal with a **generic** message (details go to stderr only) | An exception's text can carry a confidential path or content, so it never crosses the boundary. |
| Tool errors | A result with `isError: true` and `structuredContent.error = {code, retryable, message}`: `ollama_unavailable` (retryable), `model_not_found`, `embedding_dimension_mismatch`, `fts_unavailable`, `ollama_error` (retryable), `concept_not_found`, `read_failed` (retryable) | This follows the MCP convention that tool failures are results the model can see. Each code preserves the CLI's cause-specific distinctions (ADR-0018 D2). |
| Partial reads | A success result carrying `not_run`, never an error | ADR-0022: incompleteness is data. |
| `WorkspaceBusyError` | Unreachable, so no code ships | Read tools take no lock. The mapping (retryable) belongs to the first write tool. |
| Schemas | Hand-written `inputSchema` and `outputSchema` dicts in `tools.py`. Arguments are validated by a small in-repo checker for the few shapes used. Results return `structuredContent` plus a JSON `text` block | There is no `jsonschema` dependency. The text block serves clients that ignore `structuredContent`. |
| Layering | `openkos.mcp` may import `application`, `sensitivity`, `config`, `read_outcome`, and `graph`. It never imports `openkos.cli`. `application` never imports `openkos.mcp`. The CLI imports `openkos.mcp` lazily inside the verb. All of this is enforced by AST tests | ADR-0018 and ADR-0021 D1. Lazy import keeps `asyncio` off the CLI's startup path. |
| Tests | Unit tests for handlers and the gate with an injected fake `LLMBackend`, a transport and lifecycle tested with in-memory streams and `asyncio.run` inside sync tests (no `pytest-asyncio`), and a subprocess stdio tier with a poisoned `OLLAMA_HOST`, marked `cross_platform_smoke`. Coverage stays at 90% or more | This needs no new dev dependency, keeps "model-free" checkable, and covers Windows framing. |
| Specs | New `mcp` domain. Deltas to `sensitivity-aware-llm` (ADDED: the disclosure predicate), `next-action-pointer` (ADDED: structured subjects, CLI output unchanged), `query-answer` and `query-application-service` (ADDED: the optional progress callback) | `sensitivity-aware-llm` already treats embedding as egress. Disclosure is the same fail-closed authority applied at a new boundary. |
| ADRs | ADR-0027 (hand-rolled stdio, no SDK, revision 2025-11-25). ADR-0028 (disclosure over MCP is its own boundary: default hidden, launch opt-in, the conjunction with the LLM egress gates, and no locality exemption). ADR-0020 and ADR-0021 become Accepted at archive | Each decision is a public-interface or security trade-off that is hard to reverse once clients are configured. |

## Capabilities

### New Capabilities

- `mcp`:
  - the stdio transport and lifecycle (2025-11-25 handshake, `ping`, stdout hygiene);
  - the four tools and their schemas;
  - the disclosure gate at the boundary, with withheld counts, title scrubbing, and edge filtering;
  - consistency warnings;
  - the error mapping;
  - cancellation as abandonment, and progress for `query`;
  - the `openkos mcp` verb and its launch flags;
  - the enumeration guard as a stated invariant.

### Modified Capabilities

- `sensitivity-aware-llm`: ADDED "Disclosure To A Non-LLM Consumer Is Gated
  Fail-Closed". The requirement covers the predicate, the shared strict rank,
  and the rule that it takes no `include_confidential`/`local_exemption` input.
- `next-action-pointer`: ADDED "Each Recommendation And Declination Names Its
  Subjects". The field is structured and additive. The human-readable output
  stays byte-identical.
- `query-answer`: ADDED "An Optional Progress Callback Reports Phases". It
  defaults to absent, and the prompt and result stay byte-identical.
- `query-application-service`: MODIFIED "run_query" composition to thread the
  optional `progress` callback.

## Approach

The work is built bottom-up so that each slice is green on its own. The
disclosure predicate lands first as a pure leaf, with property-style tests
against `blocks_llm_send`'s rank. The transport and lifecycle land next, with
an empty tool registry. The `openkos mcp` verb and the enumeration guard (with
its canary fixture and deliberately violating tool) land before any real tool,
so every tool that follows is admitted through the guard. `get` and `navigate`
follow. `pending` needs its service to emit structured subjects before the gate
can withhold correctly. `query` needs two service-side preparations, the
backend construction move and the progress callback, before the tool itself.
Docs and the roadmap close the chain. ADR-0027 lands with the transport slice,
and ADR-0028 with the predicate slice, while their forces are fresh.

The core stays synchronous, and `asyncio` appears only under
`src/openkos/mcp/`. OKF shape knowledge stays in `model/okf.py`. The adapter
reads Knowledge Objects through services and never parses frontmatter itself.

## Affected Areas

| Area | Impact | Description |
| --- | --- | --- |
| `src/openkos/sensitivity.py` | Modified | The disclosure predicate and its set sibling |
| `src/openkos/mcp/{__init__,transport,server,tools,gate}.py` | New | The adapter |
| `src/openkos/application/backends.py` | New | Chat-client construction and local-exemption resolution, moved from the CLI |
| `src/openkos/application/next_action.py` | Modified | Structured subjects on `NextAction` and declinations |
| `src/openkos/application/query.py`, `src/openkos/retrieval/answer.py` | Modified | The optional `progress` callback |
| `src/openkos/cli/main.py` | Modified | The `mcp` verb. Call sites use `application.backends`, and behavior is unchanged |
| `tests/unit/{test_sensitivity,mcp/*,application/*,cli/*}` | New / Modified | Per slice, including the guard and the subprocess tier |
| `openspec/changes/mcp-read-surface/specs/{mcp,sensitivity-aware-llm,next-action-pointer,query-answer,query-application-service}/` | New | Specs and deltas |
| `docs/adr/0027-*.md`, `docs/adr/0028-*.md`, `docs/adr/README.md` | New / Modified | ADRs and their index rows |
| `docs/cli.md`, `docs/architecture.md`, `docs/roadmap.md` | Modified | The verb, the package, and MVP 3 status |

## Principles Impact

- **Sensitivity across boundaries.** Strengthened. A second boundary gets the
  same fail-closed authority, and confidential content is hidden by default.
- **Local-first and private.** stdio only, no network listener, no account, no
  new dependency.
- **Reconstructible.** Nothing new is persisted, and the graph is rebuilt from
  canonical files per call.
- **Human curates, engine maintains.** Read-only: the surface cannot change
  the bundle.
- **Representation, not truth; OKF; immutable `raw/`.** Untouched.

## Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| A tool path (especially an error path) leaks a confidential id or text | Med | The canary enumeration guard covers success and error paths, is proven against a violating tool, and internal errors carry a generic message. |
| A future `pending` tier omits its subjects and leaks through `reason` | Med | A tier with no declared subjects is withheld (fail closed), and a test asserts every tier declares them. |
| A disclosable object's prose names a confidential id | Med | By precedent this is not redacted ("Exclusion, Not Redaction"). It is surfaced as Product decision P1. |
| With the flag off, MCP answers are less complete than `openkos query` on a local backend, which includes confidential content | High (by design) | Documented in `docs/cli.md`. It is the binding default, and `--expose-confidential` restores parity for local backends. |
| Windows stdio framing (newline translation, pipe reads) | Med | Binary buffers, a reader thread, and a `cross_platform_smoke` subprocess test. |
| An abandoned `query` worker holds an Ollama call for up to the chat timeout | Low | ADR-0021 accepted risk. It holds no lock, and it is logged on stderr. |
| `build_graph` per `navigate` call is slow on large bundles | Low | Measured in the subprocess tier. Caching is out of scope. |
| `stale_derived_stores` hashes the whole bundle on every `query` | Low | It already runs on the CLI read path. It is measured before any change. |
| The size exceeds the 400-line budget | High | Stacked slices, below. |

## Rollback Plan

Revert the slice PRs in reverse order. Nothing is persisted, and no workspace
file, config key, or derived store changes, so there is no migration. Reverting
removes the `openkos mcp` verb, and a client configured to launch it then fails
to start the server, with no effect on the workspace. Each service-side change
(the subjects, the `progress` callback, the moved backend construction) is
additive or behavior-preserving, so reverting it is safe. ADRs still in
Proposed status are removed with their slice. After archive, a reversal takes
a superseding ADR, never an edit.

## Dependencies

- Merged: #995 read-core extraction, #1033 (`next_action` in `application/`),
  ADR-0022's `read_outcome`, and the revision-history keys in `AnswerResult`
  (#1030).
- No external dependency is added.

## Size and slices (auto-chain, stacked-to-main)

Forecast: about 2,400–2,800 authored changed lines (about 950 source, 1,300
tests, and 250 docs and ADRs; delta specs excluded).
`400-line budget risk: High`. There are seven slices, each green alone and each
at or under about 400 lines:

1. **Disclosure predicate and ADR-0028** (~250): `sensitivity.py`, its tests,
   and the ADR.
2. **Transport and lifecycle and ADR-0027** (~400): framing, dispatch,
   `initialize`/`ping`/`tools/list` (empty), the error codes, stdout hygiene,
   and stderr logging.
3. **The verb and the guard** (~380): `openkos mcp`, the flags, workspace
   validation, `gate.py`, the canary guard with its violating test tool, and
   the subprocess tier.
4. **`get` and `navigate`** (~400): the curated `get`, the neighbor tool, edge
   and row filtering, and `warnings`/`in_flight_write`.
5. **`pending`** (~350): structured subjects in `next_action` (CLI
   byte-identical), withholding at the gate, and the skip-notice count.
6. **Query preparation** (~300): `application/backends.py` (moved), and the
   `progress` kwarg on `answer`/`run_query`.
7. **`query` and docs** (~400): the tool, title scrubbing, `stale_index`,
   cancellation and progress, the error mapping, `docs/cli.md`,
   `docs/architecture.md`, and the roadmap.

## Product decisions for the orchestrator

- **P1 (open, not blocking).** A note marked public or private can mention a
  confidential note by name in its text. Over MCP, should that text be shown
  as written (the current rule for the terminal and for the local model), or
  should the whole note be hidden whenever it names a hidden one? Default if
  unanswered: shown as written. Only the `get` slice (4) depends on this.

## Success Criteria

- [ ] With `--expose-confidential` off, no response on any tool path,
      success or error, contains the confidential canary's id, title, or body
      marker. The guard demonstrably fails on the violating test tool.
- [ ] Every result carries `withheld`, and it equals the number of objects the
      gate removed.
- [ ] `pending` withholds a confidential subject's action and declinations.
      `openkos next` output is byte-identical to `main`.
- [ ] A real subprocess completes `initialize` → `initialized` → `tools/list`
      → `tools/call` over stdio, and stdout carries only JSON-RPC lines.
- [ ] A cancelled `query` gets no response and does not block later requests.
      A `query` carrying a `progressToken` receives monotonic progress
      notifications.
- [ ] A pending merge-ledger marker produces an `in_flight_write` warning, and
      the tool still answers.
- [ ] `application/` imports neither `openkos.mcp` nor `openkos.cli`.
      `openkos.mcp` imports no `openkos.cli`. `asyncio` appears only under
      `src/openkos/mcp/`. No dependency is added to `pyproject.toml`.
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .`,
      and `uv run pytest --cov` are green at 90% or more, and so is the eval
      self-test sweep. ADR-0027 and ADR-0028 are Proposed and indexed.

## Human confirmation (2026-09-26)

**P1, answered by the owner:** a non-confidential note whose text mentions a confidential note is shown **as written**. Sensitivity is per object, which matches the terminal and the local-model rule. A note that mentions something sensitive should itself be marked confidential. Only structured channels are gated: ids, titles, edges, provenance and pending subjects.
