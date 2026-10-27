---
type: Decision
title: "ADR-0078: The inbox watch in the desktop app needs a supervised daemon, so it is a design decision, not a setting"
description: Turning on the inbox watch from Settings is not a config edit. The watch runs only inside `openkos daemon`, which the shell does not supervise and which also runs the maintenance advisors. This draft proposes that the shell supervise an opt-in daemon sidecar for the chosen workspace, with the inbox folder written through the two-phase settings change, with the owner's answers on daemon scope, lifetime, workspaces and failure display.
status: Accepted
date: 2026-10-27
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-27T00:00:00Z
sensitivity: public
---

# ADR-0078: The inbox watch in the desktop app needs a supervised daemon, so it is a design decision, not a setting

- **Status:** Accepted
- **Date:** 2026-10-27

## Context

The roadmap's MVP 6 minimum scope reads "drop files (the inbox watch)"
(#1626). Dropping files on the Add files screen is built. The other half,
a way for the app user to turn on the inbox watch, looked like one more
Settings control. Reading the code shows it is not:

- **The watch is a job of `openkos daemon`, nothing else.** `unattended.inbox`
  in `openkos.yaml` is read by the job runner (`application/watch.py`); no
  other verb, and not `openkos serve`, reads it. Writing the key alone makes
  the app say "watching" while nothing watches.
- **The shell supervises one process, the engine.** ADR-0055 makes the engine
  (`openkos serve`) a supervised sidecar (`desktop/src-tauri/src/supervisor.rs`).
  The shell has no daemon, no second sidecar and no notion of one.
- **The daemon is more than the watch.** It also runs the retry of failed
  auto-commits and the maintenance pass (derived-index refresh, lint count,
  and the advisors that spend model calls inside the `unattended:` budget).
  Starting it for the watch starts all of that.
- **The settings API cannot carry the key.** `api/_settings.py` rewrites the
  five model lines of `openkos.yaml` and nothing else; the inbox path is
  validated by `config._validate_unattended_inbox` (an existing directory,
  outside `raw/`, the bundle and the workspace root) and has no preview/apply
  pair, no version token and no OpenAPI shape.
- **Consent already has an answer.** ADR-0037 defines an unattended run by
  who started it: the runner never passes a consent flag, imports a settled
  file as `ingest` would, and records every consequential proposal (a merge,
  a relation, a source supersession, a refusal) as a pending-work row that
  only a human-facing path resolves. ADR-0038/0041 keep the inbox read-only.
  So a watch started from the app imports text sources by itself, which is
  non-consequential by that definition, and nothing it queues is applied
  without the person in the Review screens.

Process supervision, lifecycle (app closed, app crashed, workspace switched,
two workspaces) and a second always-on writer are the parts ADR-0055 did not
decide. That is a design question, so no code was written for it.

## Decision

**One. The shell supervises a second sidecar, `openkos daemon`, for the
current workspace, only while the person has turned the watch on.** It reuses
the supervisor's start, stop, restart and orphan guard. Off is the default;
the daemon is never started because an inbox key happens to exist in a
workspace the person opened. It stops when the app quits or the workspace
changes. Closing the app stops the watch; a watch that outlives the app is a
service manager's job (the CLI documents it) and is out of scope.

**Two. Settings writes `unattended.inbox` through the same two-phase change
as the model settings**: a preview that names the folder, the refusals
`config` already makes, and the version; an apply that writes only that key
and commits it. "On" is the key being set; "off" removes it. The folder is
chosen with the native folder chooser the shell already uses for files.

**Three. The control says what happens, in `docs/decision-wording.md`
terms.** Files with a text-source extension that stay unchanged for the quiet
window are added to the knowledge base on their own, using the AI model, up to
the unattended budget; other files are ignored; the folder is never changed. A
file edited after it was added becomes a new version. Merges, relations and
retiring an older version are never applied: they wait in the review queue.

**Four. The sidecar runs `openkos daemon` whole, and its other jobs are named,
not hidden.** No watch-only mode is added to the CLI. Settings says that turning
the watch on also retries failed auto-commits and runs the maintenance pass,
whose advisors spend model calls inside the `unattended:` budget.

**Five. A stopped daemon is visible without looking for it.** The app shows a
persistent watch status (on, stopped by the person, stopped on the budget,
failed), and a daemon that stops on its budget or on a failure also raises a
macOS notification. A full job-outcome view like `openkos pending` is not part
of this decision.

## Consequences

**Easier.** The roadmap sentence becomes true without a terminal. The consent
model needs no change: the daemon already enqueues instead of applying.

**Harder.** A second long-lived writer next to `serve` (the workspace lock is
held only for short commit phases, so they coexist, ADR-0020). A daemon that
fails or exhausts its budget needs a place in the app to be seen (today
`openkos pending` shows job outcomes; the app has no equivalent). The shell
gains lifecycle states (starting, running, stopped by the person, crashed) and
tests for them, including the orphan case ADR-0055 measured for the engine.
The engine and shell contracts (OpenAPI snapshot, sidecar manifest, Rust
supervisor tests) all change.

**Owner decisions (2026-10-27).** The whole daemon, not a watch-only mode. The
watch stops with the app; no login item. One daemon, for the current workspace
only; switching workspace restarts it. A stop on budget or failure shows in a
persistent status and a macOS notification.

## Alternatives considered

**Write the key and tell the person to run `openkos daemon`.** The smallest
change. Rejected as the app's answer: the audience is a non-terminal user, and
a switch that does nothing by itself is the failure this issue exists to close.

**Run one watch pass on demand ("Check the folder now") through the local
API.** No supervision, no new process; the engine already calls synchronous
application services per request. It gives a folder import but not a watch, so
it does not meet the roadmap's wording. A reasonable first slice if the owner
wants something before the supervision design lands.

**Have `serve` run the watch in a thread.** One process, no second sidecar.
Rejected: it makes the adapter a job runner, against ADR-0021's boundary
(async only at the adapter edge, services synchronous) and ADR-0018's
refusal of a single orchestrator.
