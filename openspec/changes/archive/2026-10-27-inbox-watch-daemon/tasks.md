# Tasks: inbox-watch-daemon

Refs #1626. Proposal: `proposal.md`. Specs: `specs/local-http-api`,
`specs/desktop-shell`, `specs/folder-watch`. ADR-0078. The Settings requirement is added with its slice (task 5).

Strict TDD is ON, runner `uv run pytest` and `cargo test`. No test reaches a
model.

## Delivered

- [x] 1. [TEST+IMPL] `config.set_unattended_inbox` and the typed
  `InboxRejected` reasons. RED observed; a surviving mutation (unterminated
  final line) was closed with its own test.
- [x] 2. [TEST+IMPL] `application/inbox_settings.py`, `openkos.api` and
  `/v1/settings/inbox`, the OpenAPI snapshot, the API surface golden and the
  generated client schema.
- [x] 3. [TEST+IMPL] `openkos daemon --stop-on-stdin-eof`.
- [x] 4. [TEST+IMPL] `watch.rs`: the supervisor, the remembered choice, the
  daemon locator, `engine::start_daemon`, the shell commands and the quit hook.

## Remaining

- [ ] 5. [TEST+IMPL] The desktop client wrappers for `/v1/settings/inbox` and
  the shell commands, and the Settings "Watch a folder" card: wording from
  `docs/decision-wording.md`, the native folder chooser, preview and apply, the
  daemon's other jobs named.
- [ ] 6. [TEST+IMPL] The persistent watch status (on, stopped by the person,
  stopped on the budget, failed), including the engine-side read of the last
  watch job outcome for "stopped on the budget".
- [ ] 7. [TEST+IMPL] The macOS notification on a budget or failure stop, raised
  from the supervisor's `on_failed` seam.
- [ ] 8. [DOC] `docs/cli.md`, `docs/architecture.md` if the shape changed, the
  roadmap sentence, and the archive of this change when its specs are met.
