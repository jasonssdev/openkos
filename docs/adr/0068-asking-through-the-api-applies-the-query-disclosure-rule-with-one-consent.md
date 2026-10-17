---
type: Decision
title: "ADR-0068: Asking through the API applies the query disclosure rule, with one consent"
description: openkos.api.ask gates its own answer with the rule the MCP query tool and query --json share, reused from application.query_report; include_confidential is the one consent for both the model and the answer; the local API serves it as a pollable job.
status: Proposed
date: 2026-10-16
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-16T00:00:00Z
sensitivity: public
---

# ADR-0068: Asking through the API applies the query disclosure rule, with one consent

- **Status:** Proposed
- **Date:** 2026-10-16

## Context

The desktop app needs to ask questions, and `openkos.api` has no ask operation. [ADR-0058](0058-the-local-api-is-loopback-http-with-a-per-launch-bearer-token-and-host-origin-checks.md) left one obligation for this moment: the first ask or query endpoint must apply the sensitivity disclosure gate before an answer crosses the boundary. [ADR-0060](0060-the-stable-python-api-is-a-versioned-facade-with-one-error-root.md) made the Python API owner-local and gate-free, and left gating to a disclosing adapter.

Two constraints meet here. The local API adapter may import only `openkos.api` among the engine's modules ([ADR-0061](0061-the-local-api-is-a-fastapi-adapter-over-the-python-api-shipped-as-an-optional-extra.md)), so it cannot reach a gate on its own. The API's import guard bars `openkos.mcp.gate` and `openkos.sensitivity`. The rule itself already exists twice: the MCP `query` tool's `disclose_query` and `application/query_report.py`, which `query --json` uses and which states it follows the MCP rule on the same allowed-set primitive.

The CLI asks for one consent, `--include-confidential`, and `--json` reads it for both questions: whether confidential concepts may enter retrieval (and so reach a model that is not verifiably local), and whether the machine-readable answer may name them. A local model already receives confidential concepts through the local exemption without the flag, and `--json` then withholds the answer.

A query takes seconds to minutes against a local model, and the local API already runs the long judging operation as a pollable job on its own thread.

## Decision

- `openkos.api.ask` gates its own answer. It calls the query service, then builds its `Answer` from `application.query_report.build_query_report` with the disclosable set taken after the query ran. A confidential concept is never named, titled or quoted without consent, only counted, and an answer whose prompt held one is withheld. This is the one result of the API that is gated; every other result stays owner-local.
- `include_confidential` is the one consent, with the CLI's meaning: confidential concepts may enter retrieval and may be named in the answer. Without it, the shared resolver's local exemption still decides whether a local model sees them.
- The local API serves an ask as a single pollable job (`POST /v1/ask`, `GET /v1/ask/{id}`), at most one active, on its own daemon thread. An ask has no stop: the query service has no point at which to observe one.

## Consequences

**What becomes easier:**

- The adapter, the TypeScript client and any later client get an answer that is already safe to show; none of them can forget a gate.
- One implementation of the rule serves `query --json` and the API, so they cannot drift.

**What becomes harder, and what we accept:**

- On a local model, an answer that drew on a confidential concept is withheld until the person consents, even though nothing left the machine. The app must offer that consent. It is the conservative reading of ADR-0058.
- The same consent that names a confidential concept also lets it reach a remote model. A client that shows the backend's locality beside the consent makes this legible; the API does not split it.
- The API now holds one disclosure decision, so a change to the rule changes the API's result. The rule lives in `query_report` and is pinned by its tests and the API's.
- An ask cannot be cancelled; an abandoned one runs to the end on a daemon thread and writes nothing.

## Alternatives considered

- **Gate in the adapter.** The adapter cannot import a gate, and widening its import rule would put a second disclosure seam outside the API.
- **An ungated `ask` plus a public gating function.** Two calls, and a client that forgets the second shows confidential text.
- **Reuse `mcp/gate.py` directly.** The API's import guard bars the MCP package; `query_report` is the same rule inside `application/`.
- **Two consents (send to the model; show in the answer).** The CLI has one, and "send it to a remote model but hide it from its owner" has no user.
- **A synchronous route.** Holds a request thread for the length of a model call, which the judging job already avoids.
