# Proposal: Ask Through `openkos.api` And The Local API

Tracking issue: #1483. Governing decisions: ADR-0018 (application services),
ADR-0058 (the local API, which requires the disclosure gate on the first ask
endpoint), ADR-0060 (the versioned Python API), ADR-0061 (the local API adapter),
ADR-0062 (the query limit default), ADR-0065 (the generated TypeScript client).
New decision record: ADR-0068 (Proposed, written with this plan). Paths are under
`src/openkos` unless they start with `tests/`, `docs/`, `desktop/` or `openspec/`.

## Intent

MVP 6's desktop app must do everything a non-terminal user needs, and asking the
knowledge base a question is the most basic of those. The terminal asks with
`openkos query`, another agent asks through the MCP server's `query` tool, but
`openkos.api` has no ask operation, so neither the local HTTP API nor the
desktop client can ask at all.

## Scope

### In scope

- **`openkos.api.ask(workspace, question, ...)`**, a synchronous wrapper over
  `application.query.run_query`, the service the CLI and the MCP tool already
  call. It returns an API-owned `Answer`: the outcome (`answered`, `no_match`,
  `no_answer_in_context`, `withheld`), the answer text, the no-match cause, the
  citations, the documents the context budget left out, the attribution status,
  the withheld count, the sufficiency check's flags, which retriever was
  unavailable and which derived indexes are stale.
- **The disclosure rule** of the MCP `query` tool and `query --json`, applied by
  `ask` itself through `application.query_report`, the CLI's implementation of
  that rule (the API may not import `openkos.mcp.gate`): without
  `include_confidential`, a confidential concept is never named, titled or
  quoted, only counted, and an answer whose prompt held one is withheld.
- **The model boundary** exactly as the CLI resolves it: confidential concepts are
  excluded from retrieval unless `include_confidential` is passed or the chat
  backend is verifiably local and the workspace keeps the local exemption on.
- **Explicit parameters** for everything `query` lets a person choose without
  `--save`: `limit`, `include_deprecated`, `include_confidential`; and a
  `progress` callback for the four answer phases.
- **The local HTTP API:** `POST /v1/ask` starts one pollable ask job (202), and
  `GET /v1/ask/{ask_id}` polls it; at most one ask is active (409 `ask_active`).
  The regenerated OpenAPI snapshot.
- **The TypeScript client:** the regenerated types and `ask` / `askJob` methods
  with their tests.
- **Docs:** ADR-0068, the CHANGELOG, the one-line shape statements that say the
  API and the local API apply no disclosure gate.

### Out of scope

- **`query --save`** (filing an answer back as an `Insight`) through the API: a
  write with its own preview, grounding gate and consent, left for its own change.
- **The desktop ask screen.** Another change owns `desktop/ui/`.
- **Stopping an ask.** The query service has no stop point; a model call is
  never preempted. A client abandons an ask by not polling it.
- **The CLI.** No verb changes; `docs/cli.md` does not change.

## Approach

`ask` reads the configuration afresh, builds the chat and embedding clients
through the API's own composition root (`_wiring`, the resolver the CLI and MCP
use), resolves the local exemption with `backends.resolve_local_exemption`,
reads which derived indexes are stale, calls `run_query`, and only then takes the
disclosable set and builds the report with `query_report.build_query_report`.
`Answer` is mapped from that report, so the disclosure decision lives in one
existing place. The HTTP job follows the judging-run registry pattern on its own
daemon thread, so a slow model holds no request thread.

## Risks

- **A withheld answer on a local model.** With a local backend the exemption
  lets a confidential concept into the prompt, and without `include_confidential`
  the answer is withheld, as `query --json` does. The app must offer the consent
  for an answer to show. This is the conservative reading of ADR-0058.
- **Surface growth.** Every new dataclass field freezes within v1.
- **Concurrent changes** to `api/__init__.py`, the OpenAPI snapshot and the
  generated client (#1475): resolved by keeping both sides and regenerating.
