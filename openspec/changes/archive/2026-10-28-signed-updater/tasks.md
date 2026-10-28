# Tasks: signed-updater

Refs #1624. Proposal: `proposal.md`. Specs: `specs/desktop-shell`,
`specs/desktop-settings`, `specs/desktop-client`, `specs/desktop-packaging`.
ADR-0079, ADR-0080.

Strict TDD is ON, runner `uv run pytest`, `cargo test` and `pnpm test`.

## Delivered

- [x] 1. [TEST+IMPL] `desktop/scripts/update_feed.py`, `build-app.sh
  --release`, the `desktop-release` job in the `release` environment and the
  throwaway-key path in CI's bundle job. RED observed for the feed, the
  workflow and the script's refusals; mutations of the signature strip, the
  environment name and the password check were killed. Measured locally with a
  throwaway key: the archive unpacked to a copy that passes
  `codesign --verify --deep --strict`, has no `._` entries, and its signature
  verifies with `minisign-verify`, carrying `version:0.0.1`.
- [x] 2. [TEST+IMPL] `update.rs` (the choice, the gated check, the held
  update), the four shell commands, `tauri-plugin-updater` `=2.13.3`, the
  updater config with a marked placeholder public key and
  `requireSignedVersion`. RED observed for the config tests; the off-guard
  mutation was killed. The tests of the choice and the gate were written with
  the module, so their RED was shown by mutation, not by a run before it.
- [x] 3. [TEST+IMPL] `desktop/client/update.ts`, the Settings Updates card and
  its confirmation, the sidebar entry and the launch check. RED observed at
  each layer; mutations of the confirmation guard, the dialog guard and the
  launch check were killed.
- [x] 4. [DOC] `docs/install-desktop.md`, `docs/decision-wording.md`,
  `docs/roadmap.md`, `docs/architecture.md`, ADR-0080; the deltas merged into
  `openspec/specs/` and the change archived.

## Owed

- The owner generates the key, creates the `release` environment with its two
  secrets and commits the public key (`docs/install-desktop.md`, "For
  maintainers").
- The first real release is checked end to end: the previous build offers it,
  installs it and restarts, and whether macOS asks for approval again.
