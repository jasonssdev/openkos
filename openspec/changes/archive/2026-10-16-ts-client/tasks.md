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

- [x] S2.1 `desktop/package.json` (exact pins, `packageManager`, `engines`),
  `.node-version`, `tsconfig.json`, `pnpm-lock.yaml`; `.gitignore` for
  `desktop/node_modules/` and `desktop/dist/` (D1, D9).
- [x] S2.2 `scripts/schema.mjs` with `generate` and `--check`; generate
  `client/schema.d.ts` (C1, D3). RED: `check:schema` exited 1 on a missing
  file, on a hand-edited file (`code: number`) and on a snapshot with a field
  added to `ErrorBody`; 0 after `generate` and after each restore.
- [x] S2.3 `client/errors.ts`: `ERROR_CODES`, `ApiError`, `ProtocolError`,
  `EngineUnreachable`, the predicates; the error-code test against
  `_errors.py` (C1, C3, D3, D4). RED: both test files failed to load
  (`Cannot find module './errors.ts'`, `'./index.ts'`) and `tsc` reported
  TS2307 for each missing module.
- [x] S2.4 `client/engine.ts` and `client/index.ts`: `createClient(ready)`, the
  base URL check, the token in the `fetch` closure, one method per operation
  over the hand-written `call`, envelope mapping, `retryWhileBusy` (C2, C3, D2,
  D4, D5, D6). Both protocol checks (a success that is not JSON, a failure
  without the envelope) run where the request is sent, so the error names its
  path. GREEN: 63 tests. The first GREEN was over openapi-fetch; S2.5's
  type-equality test then failed on three methods (TS2344; openapi-fetch's
  mapped types widen the schema's tuples to arrays), so the runtime
  became the hand-written call and openapi-fetch was removed (D2).
- [x] S2.5 Type-level tests. `plan-kind.typecheck.ts`: reading `plan.tier` in
  the `merge` branch fails TS2339; dropping the `keep_distinct` case fails
  TS2322 on the `never` branch. `types.typecheck.ts`: every method takes and
  returns exactly `Schemas[...]`; RED under openapi-fetch (three TS2344).
- [x] S2.6 Node CI job (C4, D8); the page build step joins it in S3.
- [x] S2.7 Mutations on the exact guarded lines, each restored from a byte
  copy: 24, all killed (the host, the scheme, the path, credentials/query/hash,
  the envelope's `retryable` type, the non-envelope failure, the non-JSON
  success, the busy-only retry, the deadline arithmetic, the `Retry-After`
  pattern, the `Authorization` header, `retryAfterSeconds`, both defaults, the
  `isBusy`, `isTryAgain`, `isStale` and `isUnmergeRefusal` code sets, one
  missing code, the missing path value, path encoding, the query, the JSON
  content type, a wrong path template). The content-type mutation survived the
  first run and got its own test.

## S3 The Page

- [x] S3.1 `ui/view.ts` with its tests, `ui/main.ts` over the client,
  `index.html` loading the module; `vite.config.ts` (root `ui`, out `dist`)
  (W1, D7). RED: `ui/view.test.ts` failed to load (`Cannot find module
  './view.ts'`). GREEN: 76 tests. Mutations, each restored from a byte copy: 5,
  all killed (each failure branch, the unknown-error fallback showing the
  error's text, the open-decision sum, the restart attempt number).
- [x] S3.2 `tauri.conf.json` `frontendDist` to `../dist`; remove `ui/main.js`.
  `cargo fmt --check`, `clippy -D warnings` and `cargo test` (42) pass over the
  built page; the debug binary carries `/assets/index-*.js` and `.css`.
- [x] S3.3 `build-app.sh` installs and builds the page, and fails without
  `dist/index.html` (B1). The page section, run with a `pnpm` that builds
  nothing, exits 1 naming the missing file; with one that writes it, passes.
- [x] S3.4 The `desktop` and `desktop-bundle` jobs set up Node and pnpm and
  build the page; the `desktop client` job builds it too (D8).
- [x] S3.5 `docs/architecture.md` (the `desktop/` tree and the page bullet),
  `CONTRIBUTING.md` (the page build in the shell's dev loop).

## S4 Archive

- [x] S4.1 CHANGELOG, roadmap status line.
- [x] S4.2 Merge the delta specs into `openspec/specs/` (new `desktop-client`;
  modified `desktop-shell` and `desktop-packaging`); name-match every heading;
  check each requirement against the shipped code.
- [x] S4.3 ADR-0065 Accepted in all three places.
- [x] S4.4 Move the change to `openspec/changes/archive/<date>-ts-client/` with
  `archive-report.md`.
