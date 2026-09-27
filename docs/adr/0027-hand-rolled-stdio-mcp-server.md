---
type: Decision
title: "ADR-0027: The MCP adapter is a hand-rolled stdio server for protocol revision 2025-11-25, with no SDK"
description: OpenKOS serves MCP from its own stdlib-only package under src/openkos/mcp/ -- newline-delimited JSON-RPC over stdio, tools only, targeting revision 2025-11-25 -- instead of adopting the official SDK, and it owns stdout structurally while serving.
status: Proposed
date: 2026-09-26
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-09-26T00:00:00Z
sensitivity: public
---

# ADR-0027: The MCP adapter is a hand-rolled stdio server for protocol revision 2025-11-25, with no SDK

- **Status:** Proposed
- **Date:** 2026-09-26

## Context

MVP 3's last deliverable is an adapter that lets a chat client the user already has ask the knowledge base questions. ADR-0021 settled the sync/async boundary and deliberately left one question open: which MCP SDK to adopt, and whether to adopt one at all. That question is a dependency decision, and it has to be settled before the first line of the adapter.

The forces:

- **The official SDK is heavy for what we need.** `mcp` 2.2.0 was measured: it pulls 28 distributions, including `pydantic`, `anyio`, `starlette`, `uvicorn`, `httpx` and `jsonschema`, and costs about 250 ms to import. `AGENTS.md` states that `pydantic` is deliberately not a dependency, and the project has six direct dependencies today.
- **The surface we need is small.** The adapter is read-only and tools-only: `initialize`, `ping`, `tools/list`, `tools/call`, outbound progress and inbound cancellation. There are no resources, prompts, sampling, elicitation, roots, completion, logging capability, HTTP transport, or authentication in scope.
- **The protocol is moving.** Revision 2025-11-25 uses an `initialize`/`initialized` handshake and is what deployed clients speak. A later revision (2026-07-28) is stateless. Supporting both, or choosing by inspecting the first message, is unverified and is a separate piece of work.
- **stdio has two sharp edges.** Any byte written to stdout that is not a protocol message corrupts the stream, and the codebase has many `typer.echo`/`print` paths below the adapter. On Windows, text-mode streams translate `\n` to `\r\n`, and asyncio's pipe reading on the Proactor loop is unreliable for console and stdio handles.
- **ADR-0021's rules still hold.** The adapter owns the only event loop, calls the synchronous layer through one worker thread per operation, and treats cancellation as abandonment.

## Decision

**We implement the MCP server ourselves, in `src/openkos/mcp/`, with the standard library only.** `asyncio`, `json`, `threading` and `logging` are the whole toolkit. No dependency is added to `pyproject.toml`. `asyncio` appears nowhere outside this package, and the CLI imports the package lazily inside the `openkos mcp` verb, so it stays off every other command's start-up path.

**We speak newline-delimited JSON-RPC 2.0 over stdio, and we target protocol revision 2025-11-25 only.** An `initialize` that names any other version is answered with `2025-11-25`, which is the negotiation that revision defines; the client decides whether to continue. The server declares only the `tools` capability, with `listChanged: false`. JSON-RPC batches are rejected with `-32600`, because batching is absent from the targeted revision. `ping` is answered, because the revision makes that a MUST for the receiver.

**The package has four modules, each with one reason to change.** `transport.py` owns framing and the process's standard streams. `server.py` owns the lifecycle, dispatch, error mapping, cancellation and progress. `tools.py` owns the tool registry, the hand-written input and output schemas, and a small in-repo argument validator. `gate.py` owns disclosure (ADR-0028).

**Stdin is read by a dedicated reader thread that feeds an `asyncio.Queue`, and stdout is owned structurally.** While serving, the transport duplicates file descriptor 1 into a private handle, points descriptor 1 at stderr, and rebinds `sys.stdout` to `sys.stderr`. Protocol messages are written as UTF-8 bytes to the private handle from the event-loop thread only. A stray `print`, a `typer.echo`, or a child process inheriting descriptor 1 therefore lands on stderr and cannot corrupt the protocol stream. Diagnostics go to stderr through stdlib `logging`.

**Each tool call runs on its own daemon worker thread.** This is ADR-0021's "one worker thread per operation" in the form a read-only surface needs. A cancelled request is abandoned, not stopped, and no response is sent for it. On end of input, in-flight requests are abandoned and the process exits without joining their workers. The daemon choice is safe **only because every tool is read-only**: an abandoned read writes nothing, so ending it with the process cannot tear a write.

**Tool schemas are hand-written dictionaries, and arguments are checked by an in-repo validator that supports a declared keyword set.** A schema that uses a keyword outside that set fails a test instead of being silently unchecked. Results carry `structuredContent` and the same JSON in a `text` content block, for clients that ignore structured content.

## Consequences

Easier:

- No dependency, no `pydantic`, and no measurable import cost for the rest of the CLI.
- Full control over the two stdio hazards: stdout cannot be corrupted by code below the adapter, and framing is byte-exact on every platform.
- The whole protocol surface fits in a few hundred lines that a reviewer can read, and every error path is ours to make disclosure-safe (ADR-0028).

Harder, or accepted:

- We own protocol conformance. A new revision, or a capability outside tools, is our work, and nothing upstream will do it for us. The stateless 2026-07-28 revision is a named follow-up, not something this decision supports.
- The first write tool must revisit the worker model. A daemon worker killed at process exit in the middle of a write would leave a torn write. That tool needs workers that the process joins before exiting, or a lock-aware shutdown, and it must carry ADR-0020's lock rules. Nothing in this decision may be read as permitting daemon workers for writes.
- Abandoned read workers are unbounded, as ADR-0021 already accepted. A cancelled `query` can hold an Ollama call for up to the chat timeout.
- The argument validator is deliberately small. Adding a richer schema construct means extending the validator and its keyword list in the same change.

## Alternatives considered

- **The official `mcp` SDK.** It is the default choice and would track protocol revisions for us. Rejected on dependency weight: 28 distributions including `pydantic`, which `AGENTS.md` excludes, and a measured import cost of about 250 ms, for a surface that uses a small fraction of it.
- **Another third-party MCP framework.** The candidates build on the same stack, including `pydantic`, so they inherit the same objection with less upstream authority.
- **A synchronous server with no event loop.** Rejected by ADR-0021: it would serialize every request behind the slowest one and make a later concurrent transport a rewrite.
- **asyncio's own pipe transport for stdin** (`connect_read_pipe`). Rejected because the Proactor loop on Windows does not read console or stdio handles reliably. A reader thread works the same way on every platform.
- **`jsonschema` for argument validation.** Rejected as a new dependency for four small, fixed schemas.
- **Supporting several revisions by inspecting the first message.** Rejected for now as unverified. It can be added later without changing anything decided here.
