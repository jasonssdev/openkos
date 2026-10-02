---
type: Decision
title: "ADR-0040: The inbox watch may be woken by an optional OS file-notification backend; polling stays the default, the fallback and the safety net"
description: An optional openkos[watch] extra (watchdog) and an unattended.watch_backend key let the daemon end its idle wait on an OS event; the event only triggers the ordinary polling pass, which still does every settle and re-hash check, and the periodic poll keeps running, so the backend can lower latency but never lose a file or change a guarantee of ADR-0038.
status: Proposed
date: 2026-10-01
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-01T00:00:00Z
sensitivity: public
---

# ADR-0040: The inbox watch may be woken by an optional OS file-notification backend; polling stays the default, the fallback and the safety net

- **Status:** Proposed
- **Date:** 2026-10-01

## Context

The inbox watch (ADR-0038) polls: each pass stats every inbox file and
settles them over a quiet window. That is portable and simple, but it reacts
only at the next poll and costs a scan per pass (#1213). Operating systems
offer change notification (FSEvents, inotify, ReadDirectoryChangesW), but the
three differ in what they report, can drop or coalesce events, and are
unreliable on network and synced folders, which are common inboxes. Python's
standard library has no portable binding for them. Two constraints decide the
shape: dependencies stay minimal (`docs/tech_stack.md`), and the core stays
synchronous (ADR-0021).

## Decision

**One. The backend only triggers; it never decides.** A notifier signals that
something under the inbox changed. The daemon's idle wait ends early, and the
pass that follows is the ordinary polling pass, so every settle, re-hash and
quiet-window rule of ADR-0038 holds unchanged. An event never imports, skips
or settles a file.

**Two. Polling is the default, the fallback and the safety net.** The
periodic poll keeps its interval whatever the backend, so a dropped,
coalesced or unsupported event delays a file and never loses it. A burst of
events is coalesced into one pass.

**Three. The dependency is an optional extra.** `openkos[watch]` installs
`watchdog` (Apache-2.0, widely used, covering all three OS mechanisms). It is
never a core dependency, is imported lazily, and is also in the `dev` group so
CI type-checks and smoke-tests the backend.

**Four. The choice is an `unattended.watch_backend` key, `poll` (default) or
`native`.** `native` without the extra, or a backend that cannot start (for
example an exhausted inotify watch limit), falls back to polling with a
warning on stderr and in the log; the daemon does not refuse to run. `doctor`
reports the line only when `native` is configured.

**Five. A small `typing.Protocol` seam, `ChangeNotifier`, in
`application/watch_notify.py`**, consistent with the engine's other internal
seams. The observer thread only sets a flag; the synchronous loop reads it, so
nothing async enters the core.

## Consequences

- Lower latency for a user who installs the extra and opts in, with no change
  to what is imported or when it counts as settled.
- Because the poll interval is unchanged, the backend does not by itself make
  the daemon cheaper at rest; reducing scan cost would be a separate change.
- One more optional package to track; a new extra needs a working fallback.
- Falling back instead of refusing means a typo-free but unmet `native` is
  visible only in the log, stderr and `doctor`; that is accepted for an
  unattended process that a service manager would otherwise restart-loop.

## Alternatives considered

- **Required dependency.** Rejected: conflicts with minimal dependencies and
  gains nothing polling does not already do correctly.
- **Stdlib-only per-OS bindings (ctypes/select).** Rejected: three
  platform-specific code paths to maintain and test for a convenience.
- **Event-driven imports without the poll.** Rejected: OS events are lossy and
  unsupported on some filesystems; it would trade ADR-0038's guarantees for
  latency.
- **Refuse to start when `native` is unavailable.** Rejected: polling is
  correct, so a missing optional extra should degrade, not stop, an unattended
  daemon.
