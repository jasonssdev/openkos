# Tasks: A TypeScript Client For The Local API, With Node CI

Tracking issue: #1458. Inputs: `proposal.md`, `design.md` (authoritative),
`specs/desktop-client/spec.md` (a new domain), `specs/desktop-shell/spec.md`
and `specs/desktop-packaging/spec.md` (deltas), and ADR-0065 (Proposed until the
archive slice).

PR text names the change in prose ("the `ts-client` change") and uses
`Refs #1458` on every slice but the last, which uses `Closes #1458`.

Spec shorthand: `C1` The Client Is Generated From The Committed OpenAPI
Snapshot; `C2` The Client Takes Its Address And Token From The Engine State;
`C3` The Error Envelope Becomes A Typed Error; `C4` CI Holds The Client To The
Snapshot; `W1` (desktop-shell, modified) The Web View Reads The Engine State And
Calls The Local API; `B1` (desktop-packaging, modified) The App Bundle Ships The
Freeze Under Resources, Deep Ad-Hoc Signed. `D1`..`D9` are the design decisions.

Strict TDD for every behaviour with a test: observe RED (a failing test, a
failing check or a compile failure on the missing item), then GREEN, then
refactor. New guards are mutation-checked on their exact line and restored from a
byte copy.

## Review Workload Forecast

Authored changed lines are additions plus deletions; `pnpm-lock.yaml` and the
generated `schema.d.ts` are excluded. The ~400-line figure is advisory.

| Slice | Title | Forecast (authored) | Depends on |
|-------|-------|---------------------|------------|
| S1 | Plan: proposal, design, delta specs, tasks, ADR-0065 | ~600 (docs) | none |
| S2 | The client, its tests, the drift check, the Node CI job | ~600 | S1 |
| S3 | The page on Vite and the client; shell build and CI; docs | ~350 | S2 |
| S4 | CHANGELOG, roadmap, spec merge, ADR-0065 Accepted, archive | ~250 | S3 |

Delivery strategy: stacked-to-main.

## S1 Plan

- [x] S1.1 Write `proposal.md`, `design.md`, `tasks.md`, the delta specs and
  ADR-0065 (Proposed, indexed).

## S2 The Client

- [ ] S2.1 `desktop/package.json` (exact pins, `packageManager`, `engines`),
  `.node-version`, `tsconfig.json`, `pnpm-lock.yaml`; `.gitignore` for
  `desktop/node_modules/` and `desktop/dist/` (D1, D9).
- [ ] S2.2 `scripts/schema.mjs` with `generate` and `--check`; generate
  `client/schema.d.ts` (C1, D3). RED: `check:schema` fails on a missing and on an
  edited file.
- [ ] S2.3 `client/errors.ts`: `ERROR_CODES`, `ApiError`, `ProtocolError`,
  `EngineUnreachable`, the predicates; the error-code test against
  `_errors.py` (C1, C3, D3, D4). RED first.
- [ ] S2.4 `client/engine.ts` and `client/index.ts`: `createClient(ready)`, the
  base URL check, the token middleware, one method per operation, envelope
  mapping, `retryWhileBusy` (C2, C3, D4, D5, D6). RED first.
- [ ] S2.5 Type-level test: exhaustive `switch (plan.kind)` (C1).
- [ ] S2.6 Node CI job (C4, D8).
- [ ] S2.7 Mutations on the guards: the base URL check, the envelope shape
  check, the busy-only retry, the deadline, the schema comparison.

## S3 The Page

- [ ] S3.1 `ui/view.ts` with its tests, `ui/main.ts` over the client,
  `index.html` loading the module; `vite.config.ts` (root `ui`, out `dist`)
  (W1, D7). RED first.
- [ ] S3.2 `tauri.conf.json` `frontendDist` to `../dist`; remove `ui/main.js`.
- [ ] S3.3 `build-app.sh` installs and builds the page, and fails without
  `dist/index.html` (B1).
- [ ] S3.4 The `desktop` and `desktop-bundle` jobs set up Node and pnpm and
  build the page (D8).
- [ ] S3.5 `docs/architecture.md` (the `desktop/` tree and the page bullet),
  `CONTRIBUTING.md` (the page build in the shell's dev loop).

## S4 Archive

- [ ] S4.1 CHANGELOG, roadmap status line.
- [ ] S4.2 Merge the delta specs into `openspec/specs/` (new `desktop-client`;
  modified `desktop-shell` and `desktop-packaging`); name-match every heading;
  check each requirement against the shipped code.
- [ ] S4.3 ADR-0065 Accepted in all three places.
- [ ] S4.4 Move the change to `openspec/changes/archive/<date>-ts-client/` with
  `archive-report.md`.
