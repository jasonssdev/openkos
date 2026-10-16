---
type: Decision
title: "ADR-0058: The local API is loopback HTTP with a per-launch bearer token and Host/Origin checks"
description: The engine's local API binds 127.0.0.1 on a random port, requires a bearer token handed over stdin at each launch, and rejects any non-loopback Host and any unknown Origin.
status: Accepted
date: 2026-10-14
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-14T00:00:00Z
sensitivity: public
---

# ADR-0058: The local API is loopback HTTP with a per-launch bearer token and Host/Origin checks

- **Status:** Accepted
- **Date:** 2026-10-14

## Context

A desktop shell and an engine in separate processes need a transport. Anything that listens on the local machine is reachable by every local process and, through the user's browser, by every web page the user opens. Browsers let a page reach `localhost`, and a DNS-rebinding page can make a hostname it controls resolve to `127.0.0.1`, defeating the same-origin policy. This is a real class of attack against local model servers: Ollama shipped a fix for it (CVE-2024-28224). The engine holds the user's knowledge base, so it must not be that target.

The engine already has two things to reuse. The MCP adapter calls the synchronous `application/` services on one worker thread per operation ([ADR-0021](0021-sync-async-boundary.md)), and its sensitivity disclosure gate withholds what must not leave. The decision surface's payloads are owner-local ([ADR-0053](0053-curation-decisions-are-a-two-phase-application-surface.md)).

Evidence comes from a throwaway packaging spike run on the project's development machine (Apple Silicon, macOS 27.0), on the engine inside the app, on Python 3.12 and on 3.14 with identical results. Requests, in order:

| Request | Result |
|---|---|
| no token | 401 |
| bad token | 403 |
| forged Host (`evil.com`; also the right host on the wrong port) | 403 |
| bad Origin | 403 |
| `tauri://localhost` Origin with the correct token | 200 |
| correct token | 200 |
| token only in the URL | 401 |

On 3.14 the same run also showed a query answering with 200 in 2.28 s and citing the expected concept.

## Decision

The local API is HTTP on loopback with three independent checks:

1. **Bind `127.0.0.1` only, on a random port** chosen at each launch. Never `0.0.0.0`.
2. **A per-launch bearer token.** The shell generates it and hands it to the engine over stdin; it is never placed in a URL, argv or a file. Requests without it get 401; requests with a wrong one get 403. A token carried in the URL alone is not accepted.
3. **Host and Origin checks.** Reject any `Host` that is not loopback with the engine's port, and any `Origin` that is not on a short allowlist (the shell's own origin). This is what stops DNS rebinding even if a page learns the port.

The API reuses the MCP adapter's model: synchronous services, one worker per operation. Its caller is the workspace owner, as the CLI's is, so responses are owner-local: the read and decision-surface payloads ([ADR-0053](0053-curation-decisions-are-a-two-phase-application-surface.md)) carry no extra disclosure beyond what the owner already sees. The sensitivity disclosure gate is not applied to these payloads; it arrives with the first ask or query endpoint, which must apply it before any answer crosses the boundary.

## Consequences

**What becomes easier:**

- A page in the user's browser cannot call the engine: it lacks the token, and its Host and Origin are wrong.
- Another local process cannot call it without reading the shell's stdin pipe.
- The same checks cover every route, so a new endpoint is protected by default.

**What becomes harder, and what we accept:**

- **Loopback is not a boundary against the same user.** A hostile process running as the same user can inspect the engine process. The token raises the cost; it is not a sandbox.
- The shell must manage the token and the port's lifecycle, and every client other than the shell needs the token passed to it deliberately.
- Each route needs the auth middleware before it can be exposed; a route that skips it is a vulnerability.
- **Not measured:** the throughput or latency cost of the checks; a hostile DNS-rebinding page run against the engine (the Host check was tested with a forged header, not a real rebinding setup); other local-process attackers; Windows named pipes.

## Alternatives considered

- **A unix domain socket (or a named pipe on Windows).** The strongest local boundary, because filesystem permissions replace a token and no browser can reach it. Not tried in the spike, and the web view's fetch cannot speak to a socket without a shim. Rejected for the first release; revisit once the shell can proxy requests over it.
- **stdio only.** No network surface at all, but it cannot serve concurrent requests, long-running streams, or clients beyond the shell, and it would need a protocol of our own on top. Rejected.
- **No auth, with an Origin allowlist (Ollama's model).** Simple, and a rebinding request from a foreign Origin is refused, but any local process, and any client that sets its own headers, is let in. Rejected: the Origin check alone leaves the API open to anything that can send the right headers, and a token handed over stdin costs little.
- **A token in the URL or a query string.** Leaks into logs, history and referrers. The spike confirmed the server does not accept it.
