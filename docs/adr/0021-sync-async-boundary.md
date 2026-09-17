---
type: Decision
title: "ADR-0021: The sync/async boundary -- a worker thread per operation, and no cancellation below it"
description: The application layer stays synchronous and the MCP adapter owns the only event loop, calling in through one worker thread per operation; cancellation does not exist below that line and adapters must not pretend it does.
status: Proposed
date: 2026-09-17
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-09-17T00:00:00Z
sensitivity: public
---

# ADR-0021: The sync/async boundary -- a worker thread per operation, and no cancellation below it

- **Status:** Proposed
- **Date:** 2026-09-17

## Context

`docs/roadmap.md` gates MVP 3 on two ADRs before code. ADR-0020 settled
concurrency; this one settles the sync/async boundary. Half of it is
already decided: ADR-0018 states that the application layer "is
synchronous. Async lives only at the MVP 3 edge, which calls in through a
thread pool." What remains is the shape of that edge, and the shape is
constrained by four measured facts rather than by taste.

**The codebase is entirely synchronous, and deliberately so.** A search of
`src/openkos/` for `async def`, `await`, `asyncio` and `anyio` returns
nothing. The only concurrency primitive anywhere is one
`ThreadPoolExecutor` at `extraction/concept.py:2483-2489`, bounded to
`FAN_OUT_CONCURRENCY = 2` (`concept.py:2348`), fanning out per-window LLM
calls. Every blocking leaf is stdlib and has no async equivalent to switch
to: `OllamaClient` uses `urllib.request` with `"stream": False` hardcoded
(`llm/ollama.py:3`, `558`), SQLite is `sqlite3.connect`
(`state/derived.py:161`), and every git invocation goes through one
`subprocess.run` at `vcs/git.py:327`.

**Nothing below the boundary can be cancelled, and this was chosen.**
There is no deadline object, no `signal.alarm`, and no cancellation path
anywhere in `src/openkos/`. What exists is narrow: a 600-second chat
timeout (`llm/ollama.py:35`, raised from 120s under #405 because 8 calls
timed out on real 6-17 KB documents), a 5-second liveness preflight
(`cli/main.py:148`), SQLite `busy_timeout` PRAGMAs, and for git subprocesses
**no timeout at all**, stated as a decision at `vcs/git.py:336-341`: a
mis-calibrated timeout could kill an in-flight `git filter-repo` rewrite
mid-write, so "callers control cancellation at the process level (e.g.
Ctrl-C / SIGINT) instead." Even the one existing pool only cancels futures
that have not started (`concept.py:2489`).

That premise -- a caller who can press Ctrl-C -- is exactly what an MCP
server removes, and the consequence was measured rather than assumed.
Running `asyncio.to_thread` over the real `workspace_lock` and cancelling
the task:

| moment | observed |
| --- | --- |
| `await task` after `task.cancel()` | `CancelledError` -- the request ends |
| a second acquisition, immediately | `WorkspaceBusyError` |
| the worker thread | ran to completion and wrote |
| a second acquisition, after it finished | succeeds |

Cancelling the await ends the *request*, not the *work*. The abandoned
thread keeps the workspace lock for the rest of its run. An adapter that
treats cancellation as a stop is reporting something false.

**The lock already gives the boundary mutual exclusion, and already
forbids composition.** `workspace_lock` opens a fresh descriptor per call
(`lock.py:156-157`) and `flock` binds the open file description, not the
process, so two threads of one process contend exactly as two processes
do. Measured directly: with thread A holding the lock, thread B raised
`WorkspaceBusyError`. The same mechanism makes the contextmanager
**non-reentrant** -- a nested acquisition on one thread refuses, also
measured -- and the refusal it raises says "another OpenKOS process is
modifying this workspace right now", which in that case is untrue.

**The single-workspace-per-process assumption lives at exactly one line,
repeated.** `Path.cwd()` has 30 call sites and every one is in
`cli/main.py`, always `root = Path.cwd()` as a verb's opening line. No
module under `application/`, `state/`, `bundle/`, `vcs/`, `graph/`,
`resolution/` or `retrieval/` reads the process's directory; they all take
an explicit `root`. The assumption an MCP server must not inherit is
therefore confined to the layer that adapter replaces anyway.

## Decision

**One. The application layer stays synchronous, and gains no async
variants.** No `async def` appears in `application/` or anything below it.
This restates ADR-0018 as a boundary rather than a preference: a service
with both a sync and an async form is two implementations of the same
rules, which is the duplication the layer exists to prevent.

**Two. The MCP adapter owns the only event loop, and calls into the layer
through one worker thread per operation** (`asyncio.to_thread`, or the
`anyio` equivalent if the SDK supplies the loop). The adapter is the only
async code in the repository.

**Three. Cancellation does not exist below the boundary, and the adapter
must not pretend it does.** A cancelled request is **abandoned, not
stopped**. Concretely, on cancellation the adapter must not report that
the workspace is unchanged, must not assume the lock is free, must not
retry the same operation, and must not start a compensating one. A
cancelled `ingest` can still write, and a truthful adapter says the
outcome is unknown until the abandoned operation ends. This is the
decision most likely to be quietly violated later, because every async
framework makes cancellation look like it works.

**Four. Write concurrency inside the server is bounded by the lock, not by
the pool.** Because same-process threads contend, a thread pool needs no
additional mutex for writes and must not grow one; adding a second
exclusion mechanism would create two sources of truth about who is
writing. `WorkspaceBusyError` reaching the adapter is normal contention,
handled by ADR-0020's Decision Five, not an internal error.

**Five. No MCP tool may compose two operations that each take the lock.**
The lock is not reentrant, so such a tool refuses on its second step, with
a message naming a process that does not exist. One tool is one lock
acquisition. Correcting that message to distinguish same-process
reentrance from genuine contention is follow-on work this ADR names, since
an agent will surface the text verbatim to a user.

**Six. A database connection never crosses the boundary or outlives its
worker thread.** `open_derived_connection` passes no `check_same_thread`
argument (`state/derived.py:161`), so a connection is usable only from the
thread that opened it. Every call site already opens, works and closes
within one call, which is what makes threads safe today; the rule exists
so that a future connection cache -- an obvious optimisation for a
long-lived server -- cannot introduce the bug silently.

**Seven. Progress becomes data at the boundary, or the surface is mute.**
All three mechanisms in `cli/observability.py` are gated on
`sys.stderr.isatty()` and no-op off a TTY, so an MCP client observing a
measured 4m 28s ingest (`observability.py:284`) receives nothing at all.
`phase_callback` is already the right shape -- it "writes to no stream
itself: everything goes through `sink`" (`observability.py:294`);
`progress_callback` and `stage_notice` are not. Any operation the MCP
surface exposes that can exceed a few seconds takes a progress callback as
a parameter, and the adapter translates it into protocol notifications.
No service decides whether a terminal is attached.

**Eight. The boundary adds no timeout of its own.** The existing per-call
timeouts stay as they are, including git's deliberate absence. A request
deadline the adapter cannot enforce would be a lie by Decision Three: the
await would return while the work continued.

## Consequences

**Easier.** The adapter's rule is mechanical -- resolve a root explicitly,
hand one operation to one thread, translate the result -- and it needs no
change to any existing service. Mutual exclusion and SQLite thread
affinity both hold today by construction, so the first MCP write is not
also a concurrency project.

**Harder.** A long operation cannot be interrupted by the client that
started it, which is a property of the system rather than a bug in the
adapter, and it must be documented as such before someone files it as
one. The 600-second chat timeout becomes the effective upper bound on how
long an abandoned thread can hold the workspace lock -- acceptable for
MVP 3, and the first number to revisit if it is not.

**Accepted risk.** A server under load can accumulate abandoned threads
from cancelled requests, each still holding its place in the queue for the
lock. Nothing bounds that today beyond the pool's own size, and this ADR
does not add a bound, because a limit chosen before any measurement would
be arbitrary. The first MCP load measurement should report it.

**Explicitly not decided here.** Which MCP SDK to adopt, and whether to
adopt one at all. The official `mcp` package (2.2.0) requires `pydantic`,
`anyio`, `httpx2`, `starlette`, `uvicorn`, `jsonschema`,
`opentelemetry-api` and more, against six direct dependencies today -- and
`AGENTS.md:43` states that `pydantic` and `instructor` "are deliberately
not dependencies." That tension is a dependency decision, not a boundary
one, and every decision above holds unchanged whichever way it resolves:
a hand-written stdio server and the official SDK both call into a
synchronous layer from an event loop. Also undecided: whether the
`FAN_OUT_CONCURRENCY = 2` pool inside extraction should be reconciled with
the adapter's pool, which nothing forces until both exist.

## Alternatives considered

**Make the application layer async.** The straightforward shape for an
async server, and it would let cancellation propagate. Rejected on two
grounds. ADR-0018 already decided the layer is synchronous, and reversing
it here would be a large reversal justified by an adapter that does not
exist yet. More concretely, it buys less than it appears to: the blocking
leaves are `urllib.request`, `sqlite3` and `subprocess.run`, none of which
have stdlib async equivalents, so an async layer would wrap them in
threads anyway -- arriving at this same boundary one layer deeper, with
every service rewritten.

**Dual synchronous and asynchronous APIs.** Keeps both callers happy and
is a common library shape. Rejected as the duplication ADR-0018 exists to
prevent: two forms of every service means two places for an application
rule to drift, and the rules here are sensitivity and provenance rules.

**A process pool instead of a thread pool.** The only option that makes
cancellation *real* -- killing a worker process genuinely stops the work
-- and it is rejected on cost rather than on correctness, which is worth
recording precisely. Every call would need its arguments and results to
cross a process boundary, and neither an open SQLite connection nor an
`OllamaClient` can; a killed worker would also leave the workspace in
whatever state the kill interrupted, which is the torn write ADR-0020
works to keep detectable. If genuine cancellation ever becomes a
requirement, this is the alternative to reopen, and it should be reopened
together with Decision Three rather than in place of it.

**Running the MCP server synchronously, with no event loop.** A stdio
transport does not inherently require async, and this would erase the
boundary instead of placing it. Rejected because it makes the transport
choice irreversible: a server written this way cannot later serve a
concurrent transport without becoming the async rewrite this ADR declined,
and it would serialise every request behind the slowest one even where the
lock does not require it.
