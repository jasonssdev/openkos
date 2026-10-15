---
type: Decision
title: "ADR-0060: The stable Python API is a versioned facade package with one error root"
description: OpenKOS publishes openkos.api, a synchronous in-process facade over application/ with an integer API_VERSION, a committed surface snapshot with a compatibility classifier, API-owned result types, a Workspace handle, and one OpenKosError root that existing refusal families join without losing a base.
status: Proposed
date: 2026-10-15
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-15T00:00:00Z
sensitivity: public
---

# ADR-0060: The stable Python API is a versioned facade package with one error root

- **Status:** Proposed
- **Date:** 2026-10-15

## Context

[ADR-0039](0039-the-stable-python-api-ships-with-a-desktop-app-as-its-first-client.md) owes a Python API over the application services, versioned and declared public, shaped by the desktop app as its first client. Nothing is public today: the package has no `__all__`, `application/__init__.py` exports nothing, and the composition root (backend clients, auto-commit, commit sections) lives in `cli/main.py`, which `application/` may not import. The services already have a consumer-friendly shape (frozen dataclasses, ports with defaults, observers, `StopToken`), but they disagree on the workspace argument (`root: Path` or `WorkspaceLayout`), their refusals share no root, and several results carry internal types (`state` rows, lifecycle plans, an unversioned stored payload). The package is 0.x, so its version promises nothing. The desktop app does not exist yet, so the first shape is chosen without its real calls; the judging service ([ADR-0059](0059-judging-is-a-quote-bound-application-surface-that-persists-per-chunk-and-never-merges.md)) completed the operations the review screen needs. Separately, judging persists verdicts that only a daemon pass made visible in the queue (#1391).

## Decision

- We add `openkos.api`, a synchronous, in-process facade over `application/`. No `application/` module moves or is declared public in place. v1 covers the review queue: workspace status, the pending report, listing, previewing and applying a decision, judging quote and run, and a model-free queue refresh.
- We version the surface with an integer `API_VERSION`, independent of the package version, starting at 1. Within a value, changes are additive; a breaking change raises it in the same change. A committed snapshot of every public signature, dataclass field and exception base, with a classifier, enforces this in CI.
- We re-export application types that are already plain, leak-free contracts and wrap the rest in API-owned types; no public annotation may name an unexported engine type, a `state` type or `sqlite3`. The stored decision payload is never exposed; typed details are built from reader functions that live beside the writer.
- We add one error root, `OpenKosError`, in a leaf module. Existing refusal families and the neutral `Backend*` errors gain it as an extra base and keep every base they had. Outcomes the services return as values (`Stale`, `QuoteStale`, `RunRefused`) stay values.
- A `Workspace` handle, opened from the workspace root by the CLI's rule, replaces the `Path`/layout split. It validates the configuration once and caches nothing for the services.
- The API owns its composition: a third `BackendFactories` binding module for the chat client, the bounded-wait commit section, and the git auto-commit through a print-free core in `vcs/autocommit.py` that the CLI also calls. The CLI is not rewired onto the API.
- The surface is owner-local and applies no disclosure gate; a disclosing adapter must apply [ADR-0058](0058-the-local-api-is-loopback-http-with-a-per-launch-bearer-token-and-host-origin-checks.md)'s gate itself. In v1 no module outside `openkos.api` imports it.
- `openkos.api` names the Python API. The local HTTP API of MVP 6 is a separate adapter built on it, named in its own change.

## Consequences

**What becomes easier:**

- A client works the review queue without importing the CLI or choosing between a path and a layout.
- One `except OpenKosError` catches every documented refusal.
- An accidental breaking change fails CI.
- Later slices (ingest, query, doctor, setup) attach to the same handle, root and snapshot.

**What becomes harder, and what we accept:**

- Every re-exported dataclass freezes its fields, so an internal change to one now needs a deliberate snapshot regeneration and possibly a bump.
- Two compositions (CLI and API) coexist and are held together by a parity test, not by sharing code.
- The stored-payload readers must keep their typed results stable.
- v1 apply does not reconcile merged bodies and does not refresh the derived stores, so the index is reported stale until the next maintenance pass or reindex.
- Refresh retires only identity rows: relation-type and volatility completeness cannot be proven from persisted state, so their retirement stays with the daemon.

## Alternatives considered

- **Declaring selected `application/` modules public in place.** Almost no code, but public and owner-local helpers stay mixed and nothing enforces which is which.
- **Moving modules into a public package.** A clean boundary at the cost of heavy churn across `cli/main.py`, the daemon and `mcp/`, against "start lean".
- **Package semver as the promise.** 0.x promises nothing, and the package version moves for reasons unrelated to the API.
- **Wrapping internal exceptions in API exceptions.** Loses identity and duplicates every type.
- **Raising for stale decisions and quotes.** Contradicts [ADR-0053](0053-curation-decisions-are-a-two-phase-application-surface.md) and ADR-0059, which return them as data so a caller cannot forget to handle them.
- **Functions taking a `Path` instead of a handle.** Pushes the `Path`/layout split onto every caller and gives later slices nowhere to attach.
- **Rewiring the CLI onto the API now.** A large, byte-identity-sensitive change with no client benefit in v1.
