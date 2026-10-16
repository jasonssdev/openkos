# Tasks: The Desktop Shell Supervises The Engine Sidecar

Tracking issue: #1446. Inputs: `proposal.md`, `design.md` (authoritative),
`specs/desktop-shell/spec.md` (a new domain) and ADR-0055 (Proposed until the
archive slice).

PR text names the change in prose ("the `desktop-shell` change") and uses
`Refs #1446` on every slice but the last, which uses `Closes #1446`.

Spec shorthand: `R1` The Engine Is Located Before It Is Started; `R2` The Token
Goes Over Stdin And The Port Comes From The Ready Line; `R3` A Crashed Engine Is
Restarted Within A Bounded Budget; `R4` Quitting Stops The Engine And A Killed
Shell Leaves None; `R5` The Web View Reads The Engine State And Calls The Local
API. `D1`..`D9` are the design decisions.

Strict TDD for every behaviour with a test: observe RED (a failing test or a
compile failure on the missing item), then GREEN, then refactor. New guards are
mutation-checked on their exact line.

## Review Workload Forecast

Authored changed lines are additions plus deletions; `Cargo.lock` and the icon
are excluded. The ~400-line figure is advisory: no test or doc is cut to fit it.

| Slice | Title | Forecast (authored) | Depends on |
|-------|-------|---------------------|------------|
| S1 | Plan: proposal, design, delta spec, tasks | ~550 (docs) | none |
| S2a | The supervisor crate: engine handshake, locator, restarts, stop; CI job; scope | ~1550, about half tests | S1 |
| S2b | The Tauri app, the status page, the live engine test, CI system packages | ~550 | S2a |
| S3 | CHANGELOG, roadmap, spec merge, ADR-0055 Accepted, archive | ~250 | S2b |

S2a exceeds the advisory budget as one coherent unit: the supervisor's three
modules are only meaningful together (the supervisor drives the engine module
through the locator), and more than half of the slice is process tests. It is
labelled `size:exception`.

Delivery strategy: stacked-to-main (each slice branches from `main` after the
previous one is squash-merged).

## S1 Plan

- [x] S1.1 Write `proposal.md`, `design.md`, `tasks.md` and the delta spec.

## S2a The Supervisor Crate

- [x] S2a.1 RED: tests for the token, the ready-line parser, the handshake,
  stop (graceful, killed with its group, already exited), start failures
  (exited before ready, bad ready line, not ready, cancelled, spawn failure) and
  the stderr tail against fake `sh` engines (R2, R4).
- [x] S2a.2 RED: tests for the locator (R1).
- [x] S2a.3 RED: tests for the restart budget, the serialised state, and the
  supervisor (ready, restart with a fresh token, bounded failure, final locator
  failure, shutdown during a backoff, drop) plus the force-quit probe (R3, R4).
- [x] S2a.4 GREEN: `engine.rs`, `locator.rs`, `supervisor.rs`.
- [x] S2a.5 Mutation-check the guards on their exact lines (stdin close, process
  group, each ready-line check, window and budget comparisons, backoff cap,
  workspace check, cancellation, stderr bound); confirm the force-quit probe fails
  with a fake engine that ignores EOF.
- [x] S2a.6 CI: the `desktop` job (fmt, clippy, test).
- [x] S2a.7 Docs: the `desktop` scope in `AGENTS.md`, `CONTRIBUTING.md` and
  `openspec/config.yaml`; `docs/architecture.md` gains the shell's directory.
- [x] S2a.8 Gates: `cargo fmt --check`, `cargo clippy --locked --all-targets -- -D
  warnings`, `cargo test --locked`; `uv build` ships no `desktop/` file.

## S2b The Tauri App And The Status Page

- [ ] S2b.1 RED: a supervisor shared between threads can be shut down from any of
  them (`shutdown(&self)`), needed by the Tauri state.
- [ ] S2b.2 The Tauri app: `tauri.conf.json` (static `frontendDist`, global Tauri
  API, the CSP of D6, bundling off), `build.rs`, `main.rs`, `run()` with the
  `engine_state` command and the stop on `RunEvent::Exit` (R4, R5).
- [ ] S2b.3 The page: engine state in English, the status read with the token,
  values written as text (R5).
- [ ] S2b.4 The live test against the real engine (R5), mutation-checked with a
  foreign origin.
- [ ] S2b.5 CI: WebKitGTK packages, the build cache, the engine install and the
  live test.
- [ ] S2b.6 Docs: `docs/architecture.md` (the app and `ui/`, the delivery line),
  `CONTRIBUTING.md` (building and checking the shell).
- [ ] S2b.7 Manual check against the real engine: a SIGKILLed and a SIGTERMed
  shell leave no engine process.

## S3 Archive

- [ ] S3.1 CHANGELOG entry; the roadmap's MVP 6 status line.
- [ ] S3.2 Merge the delta into `openspec/specs/desktop-shell/spec.md`; name-match
  every heading; check every requirement against the shipped code.
- [ ] S3.3 ADR-0055 Accepted in its frontmatter, body and README row.
- [ ] S3.4 Move the change to `openspec/changes/archive/<date>-desktop-shell/` with
  `archive-report.md`; verify on `main` that the original folder is gone.
