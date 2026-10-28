# Tasks: inbox-watch-daemon

Refs #1626. Proposal: `proposal.md`. Specs: `specs/desktop-client`,
`specs/desktop-settings`, `specs/desktop-shell`, `specs/folder-watch`,
`specs/local-http-api`, `specs/stable-python-api`. ADR-0078.

Strict TDD is ON, runner `uv run pytest`, `cargo test` and `pnpm test`. No test
reaches a model.

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
- [x] 5. [TEST+IMPL] The desktop client wrappers for `/v1/settings/inbox` and
  the shell commands (`client/watch.ts`), and the Settings "Watch a folder"
  card: wording from `docs/decision-wording.md`, the native folder chooser,
  preview then confirm then apply then start, the daemon's other jobs named.
  RED observed (14 model tests, 5 render tests); a mutation of the stale-reload
  branch and of the card's "wanted" test was killed.
- [x] 6. [TEST+IMPL] The persistent watch status (on, stopped by the person,
  stopped on the budget, failed), with the engine-side read of the last
  unattended job: `application/unattended_activity.py`, `openkos.api.
  unattended_activity`, `GET /v1/unattended`, the OpenAPI snapshot, the API
  surface golden and the client schema. RED observed at each layer; mutations
  of the budgeted job kinds, of the facade's `budget_exhausted` and of the
  status derivation were killed.
- [x] 7. [TEST+IMPL] The macOS notification: `notify.rs` (fixed text, the
  supervisor's `on_failed` seam, `notify_watch_budget` for the stop only the
  page can see), `tauri-plugin-notification` pinned to `=2.6.0` and used only
  from Rust, no capability. RED observed; mutations of the failure callback and
  of the page's baseline were killed.
- [x] 8. [DOC] `docs/decision-wording.md` (the card and the status),
  `docs/architecture.md`, `docs/roadmap.md`, `docs/cli.md`; the change's deltas
  merged into `openspec/specs/` and the change archived.
