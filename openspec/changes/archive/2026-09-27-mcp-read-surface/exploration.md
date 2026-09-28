# Exploration: mcp-read-surface (#1009 + #1010)

*Produced by sdd-explore and persisted by the orchestrator. The central leak claim was spot-checked: `application/next_action.py` has no `sensitivity` import, and its reasons interpolate `f"{finding.concept_id}: {finding.detail}"` (`:594`, `:643`, `:679`).*

## Binding owner decisions
- **SDK:** hand-rolled, tools-only MCP server over **stdio**, with **no new dependency**. The official `mcp` 2.2.0 was measured (28 dists, `pydantic`, ≈250 ms import) and rejected.
- **Scope:** #1010 is a hard prerequisite and ships inside this change as its first slice.

## The four tools
| Tool | Service | Lock | LLM | Raises | Leak channels |
|---|---|---|---|---|---|
| `query` | `application/query.py:110 run_query → QueryOutcome` | none | yes (`answer.py`) | Ollama*/FtsUnavailable | citations; `omitted_titles` / `excerpted_titles` / `history_truncated_titles` |
| `get` | concept read + `list_service.py:259 list_provenance_sources → ProvenanceSources(ancestors, rows, not_run)` | none | no | id resolution only (caller-side) | `ancestors` (raw source ids); `rows` = whole bundle's id / title / sensitivity |
| `navigate` | `list_service.py:199 list_bundle_objects` + `graph build_graph().edges()/neighbors()` | none | no | `build_graph` may raise | `BundleObject` rows; `Edge.source_id` / `target_id` (the graph is sensitivity-blind by construction) |
| pending | `application/next_action.py:894 next_action → NextResult(action, declinations, skip_notices)` | none | no | no | **CONFIRMED leak:** no sensitivity import; `command` / `reason` / `declinations` interpolate concept id and lint free text |

**Also:** `BundleObject.sensitivity` is the RAW frontmatter string. The gate must call the fail-closed predicate and never branch on that field.

## #1010: a disclosure predicate
- **Placement:** a second predicate in `sensitivity.py`, still a pure leaf. Candidate names: `blocks_mcp_disclosure(value)` and `should_disclose_to_mcp_caller(metadata, *, caller_may_see_confidential=False)`, plus a set-producing sibling for bulk rows.
- **Ranking:** it reuses the STRICT fail-closed rank of `blocks_llm_send`, not the merge-floor `private`.
- **What it does not reuse:** `include_confidential` and `local_exemption`. They are model-send escapes with no honest MCP meaning.
- **Where it runs:** at the adapter boundary, filtering per object and reporting a filtered COUNT as data (ADR-0022), never content. The services stay complete, so the CLI is unchanged.
- **Test:** an enumeration guard over the adapter's tool surface. Write the violating tool first and watch the guard catch it.
- **Open tension:** the transparency title lists in `AnswerResult` (#882) do not say why a concept was held back (budget or confidentiality), so the MCP boundary must scrub confidential titles from them.

## ADR constraints
- **ADR-0020:** reads take no lock. D3 accepts that a read can observe an in-flight multi-file write, so the response should be able to say so (`bundle_ledger.scan_torn_writes`, derived staleness).
- **ADR-0021:**
  - the adapter is the only async code in the repository (`asyncio`, stdlib), with one worker thread per operation;
  - a cancelled request is abandoned, not stopped or reported as reverted (D3);
  - one tool takes one lock (D5), and no DB connection crosses the boundary (D6);
  - a long operation takes a progress callback that becomes `notifications/progress` (D7). Only `query` needs one.
- **ADR-0022:** `read_outcome.NotRun`. `get` already returns `not_run`.

## Transport
- **Framing:** newline-delimited JSON-RPC 2.0 on stdin/stdout. Logs go to stderr only; the codebase has no stderr logging convention yet, so this is new ground.
- **Dispatch:** a table of handlers, each running its service through `asyncio.to_thread`.
- **Errors:** JSON-RPC codes plus the MCP server band, including a code for a partial (`NotRun`) answer.
- **Cancellation:** `notifications/cancelled` cancels the awaiting task only.
- **Progress:** sent from the worker thread through `loop.call_soon_threadsafe`.
- **Tests:** unit-level handler tests for coverage, plus a subprocess tier that pipes JSON lines through the real process, with no network.
- **CLI:** a new `openkos mcp` verb that serves over stdio. It needs a Typer `rich_help_panel`.

## Specs and ADRs
- A new domain, `mcp`. The sensitivity disclosure requirement goes into the existing sensitivity domain if one fits (propose checks).
- **ADR-0027:** hand-rolled stdio, no SDK. ADR-0020 and ADR-0021 move to Accepted at archive.

## Slice plan (about 5 PRs)
1. The disclosure predicate plus the enumeration guard.
2. The stdio core plus the `openkos mcp` verb, with no-op tools.
3. The read tools `get`, `navigate` and pending, behind the gate.
4. The `query` tool, with progress and cancellation.
5. Docs, ADR-0027, and acceptance of ADR-0020 and ADR-0021.

## Open product decisions
- **(a)** Which protocol revision to target: 2025-11-25 (handshake), 2026-07-28 (stateless), or both by peeking at the first message (claimed cheap, not verified).
- **(b)** Whether `navigate` is one tool or two.
- **(c)** Whether an MCP caller can ever see confidential objects: never, or a launch-time opt-in.
- **(d)** Staleness and torn writes: silent, a data field, or a refusal.

## Risks
- **pending leaks today.** It is the sharpest risk found.
- **The graph needs new filtering logic;** there is no existing gate to reuse.
- **Filter `get`'s rows in the adapter,** never by mutating the shared service.
- **Peeking at the first message to pick a revision is unverified.**
- **`build_graph` rebuilds on every call.**

## Decisions (2026-09-26)
- **(c) Owner:** confidential objects are hidden over MCP by default, with an **opt-in at launch** (for example `openkos mcp --include-confidential`, exact shape left to design). The opt-in is a server-launch flag, never a per-request parameter, and never inherited from `should_block`'s hatches. Filtered objects are always reported as a count.
- **(a) Owner:** target protocol revision **2025-11-25 only**, the `initialize`/`initialized` handshake that deployed clients speak. The stateless 2026-07-28 revision is a follow-up.
- **(d) Owner:** **answer plus a warning field.** A read that may observe an in-flight multi-file write still answers, with a data field saying so and why (ADR-0020 D3, ADR-0022 style). It is not refused.
- **(b) Orchestrator default** (stated to the owner): **one** `navigate(concept_id)` tool returning the concept's neighbors (typed relations plus untyped links). Provenance stays in `get`, which keeps the roadmap's four tools.
