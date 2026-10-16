---
type: Decision
title: "ADR-0061: The local API is a FastAPI adapter over the Python API, shipped as an optional extra"
description: The local HTTP API is a thin FastAPI-on-uvicorn adapter over openkos.api only, with Pydantic confined to openkos.localapi, shipped as the optional openkos[api] extra, guarded by one ASGI layer, and launched with a stdin token and a one-line ready line.
status: Proposed
date: 2026-10-15
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-15T00:00:00Z
sensitivity: public
---

# ADR-0061: The local API is a FastAPI adapter over the Python API, shipped as an optional extra

- **Status:** Proposed
- **Date:** 2026-10-15

## Context

[ADR-0058](0058-the-local-api-is-loopback-http-with-a-per-launch-bearer-token-and-host-origin-checks.md) fixed the security shape of the local API (loopback, a per-launch bearer token, Host and Origin checks) but not the stack that serves it. `openkos.api` ([ADR-0060](0060-the-stable-python-api-is-a-versioned-facade-with-one-error-root.md)) is the operation set the API exposes. The desktop shell cannot call Python in-process, so it needs an HTTP surface with a typed schema.

A spike measured the cost of the candidate stack: 13 distributions, about 9.8 MB installed, about 8.2 MB more once frozen, about 0.32 s to the first response, and zero PyInstaller warnings. Until now the engine declared no Pydantic and no web framework.

## Decision

1. **FastAPI on uvicorn**, as a thin adapter over `openkos.api` only. It imports no `application/` module and no other engine layer.
2. **Pydantic becomes a dependency for the first time**, confined to `openkos.localapi`. The earlier rule now reads "the core and LLM extraction use no validation library"; recovery of structured model replies stays in `llm/parsing.py`.
3. **Shipped as the optional extra `openkos[api]`**, with the dev group carrying it so CI exercises it. `pip install openkos` is unchanged.
4. **Concurrency.** Short operations run on the anyio worker pool, the equivalent [ADR-0021](0021-sync-async-boundary.md) names for the MCP adapter. The judging run gets its own daemon thread. `asyncio` is imported in exactly one module, `localapi/_app.py`, whose lifespan hands the blocking run-registry shutdown to a worker thread; no other `localapi` module uses it, and `tests/unit/mcp/test_layering.py` pins both that exception and the rule for `mcp/`. There is no per-call deadline because no short operation calls a model. This deviates from the MCP server's thread-per-call, `Semaphore(4)` and deadline, and the deviation is deliberate.
5. **One ASGI guard** in the order Host, CORS preflight, Origin, token. The preflight is answered before the token check and never before the Host check. Origins are restricted to the Tauri allowlist.
6. **Launch.** The token arrives on stdin and only its sha256 is kept in memory. The port-0 bind is handed to uvicorn, a one-line JSON ready line announces the address, and stdin EOF and SIGTERM are the shutdown lifelines.
7. **`opentelemetry-api` arrives as a transitive hard dependency of FastAPI** with the extra.

[ADR-0058](0058-the-local-api-is-loopback-http-with-a-per-launch-bearer-token-and-host-origin-checks.md) is not edited and stays the authority for the security shape; this ADR records only the preflight ordering it did not cover.

## Consequences

Easier: a typed OpenAPI schema for the shell's client, request validation, dataclasses served directly, and one guard for every route.

Harder: a second serialization path (Pydantic) beside the API's dataclasses; the import guard gains one sanctioned importer; the `openapi.json` snapshot must be regenerated deliberately; the frozen app grows by about 8 MB; a stuck model call is abandoned at shutdown, never preempted; and the worker pool has no deadline, so a short operation that hangs holds its thread. That last cost is accepted because no short operation calls a model.

## Alternatives considered

- **Starlette alone.** Smaller (2.1 MB) but no schema and hand-written serialization; rejected because the shell's TypeScript client wants a schema.
- **The hand-rolled stdlib server pattern of [ADR-0027](0027-hand-rolled-stdio-mcp-server.md).** Rejected: HTTP/1.1 parsing, CORS and concurrency are where hand-rolled servers fail.
- **FastAPI as a core dependency.** Rejected: every `pip install openkos` would pay for a desktop-only adapter.
- **MCP's thread-per-call with a semaphore and deadline** for short operations. Rejected: there is no model call to bound. It is kept in spirit for the run's dedicated thread.
- **`CORSMiddleware` plus a separate token middleware.** Rejected: the preflight and token ordering must live in one guard.
