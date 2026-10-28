# Proposal: inbox-watch-daemon

## Intent

Refs #1626. ADR-0078 (Accepted): the roadmap's MVP 6 scope reads "drop files
(the inbox watch)". The watch is a job of `openkos daemon` and of nothing else,
so turning it on from the desktop app means the shell supervises a second
sidecar, the whole daemon, for the current workspace, only while the person
turned the watch on, and Settings writes `unattended.inbox` through the same
two-phase change as the model settings.

Success: a person who never opens a terminal chooses a folder in Settings and
turns the watch on; the daemon runs while the app is open and that workspace is
the current one; it stops when the app quits, the person turns it off or the
workspace changes; and a daemon that fails is visible without looking for it.

## Scope

### In Scope

1. Engine: `unattended.inbox` is read, previewed and applied through
   `openkos.api` (`inbox_settings`, `preview_inbox_settings`,
   `apply_inbox_settings`) and `/v1/settings/inbox`; the config validator's
   refusals carry a typed reason; the edit is comment-safe text surgery over
   that one line and commits only it.
2. Engine: a hidden `openkos daemon --stop-on-stdin-eof`, the lifeline that
   stops the daemon gracefully when its supervisor dies by any route.
3. Shell: a watch supervisor (`watch.rs`) with the states off, starting, on,
   restarting, failed and no workspace, a per-workspace remembered choice, and
   commands `watch_state`, `set_watch` and `pick_inbox_folder`.
4. Settings: the folder control in `docs/decision-wording.md` terms, naming the
   daemon's other jobs, a persistent watch status and a macOS notification on a
   stop (see `tasks.md` for what is delivered).

### Out of Scope

- A watch-only mode of the CLI, and a login item: ADR-0078, owner decisions.
- A full job-outcome view like `openkos pending`.

## Approach

The settings change copies `model_settings`' shape. The supervisor reuses the
engine's process handling and restart budget but not its handshake: a daemon
announces nothing, so it is on once it is spawned.

## Interpretations of the ADR

- **Restart on switch.** The ADR says the daemon "stops when the workspace
  changes" and that "switching workspace restarts it". Both hold if the person's
  choice is per workspace: the daemon of the old workspace stops, and one starts
  on the new workspace only where the person turned the watch on. Otherwise a
  whole daemon, and its model spend, would start on a workspace nobody chose it
  for.
- **Stopped on the budget.** The daemon never exits on a spent budget: it
  records the outcome in `.openkos/jobs.db` and idles until the budget window
  rolls. So "stopped on the budget" is a status read from the engine, not an
  exit code, and is owed with the status UI.
