# Archive Report: ts-client

**Archived**: 2026-10-16 (S4, the last slice of the change)
**Archive folder**: `openspec/changes/archive/2026-10-16-ts-client/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issue**: #1458

## Final State At Close

- **Implementation**: three slices before this one merged to `main`, each one squash-merged PR, stacked to `main` in order. S4 (this archive) closes the issue.

| Slice | PR | Squash commit | Authored lines (additions + deletions; `pnpm-lock.yaml` and `schema.d.ts` excluded) | CI `test (3.14)` | CI `desktop client` | CI `desktop bundle (macOS)` |
|-------|----|---------------|---------------------------------------------------------------------------------------|------------------|---------------------|-----------------------------|
| S1 plan, ADR-0065 | #1459 | `3f2267a4` | about 655 (docs) | 12m17s | (not yet present) | 2m08s |
| S2 the client, the drift check, the Node CI job | #1460 | `6a21ca89` | about 1,000, about 440 of them tests (`size:exception`) | 11m25s | 25s | 1m33s |
| S3 the page on Vite and the client, shell build and CI, docs | #1461 | `a452607c` | about 475 | 10m18s | 26s | 2m43s |
| S4 CHANGELOG, roadmap, spec merge, ADR-0065, archive | this PR | | docs only | | | |

- **Shipped surface**: `desktop/` as a pnpm package (`package.json`, `pnpm-lock.yaml`, `.node-version`, `tsconfig.json`, `vite.config.ts`; Node 24, pnpm 11.13.1, development dependencies only). `desktop/client/`: the generated `schema.d.ts`, `index.ts` (`createClient`, `fillPath`, `retryWhileBusy`, `Schemas`), `errors.ts` (`ERROR_CODES`, `ApiError`, `ProtocolError`, `EngineUnreachable`, `isBusy`, `isTryAgain`, `isStale`, `isUnmergeRefusal`), `engine.ts` (`EngineState`, `ReadyEngine`), their tests and two type-level tests. `desktop/scripts/schema.mjs` (`pnpm generate`, `pnpm check:schema`). `desktop/ui/main.ts` and `view.ts` replace `main.js`. Tauri's `frontendDist` is `../dist`. `build-app.sh` builds the page first. The `desktop client (typecheck, test, schema, build)` CI job; the `desktop` and `desktop-bundle` jobs set up Node and pnpm.
- **Engine**: unchanged. No file under `src/` or `tests/` was touched; `API_VERSION` stays 1 and the snapshot is unchanged.
- **ADR-0065**: Accepted, in all three places (frontmatter, the `**Status:**` body line, the `docs/adr/README.md` row).

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| desktop-client | Created | A new domain. The delta's 4 ADDED requirements became the living spec under a new Purpose section: The Client Is Generated From The Committed OpenAPI Snapshot; The Client Takes Its Address And Token From The Engine State; The Error Envelope Becomes A Typed Error; CI Holds The Client To The Snapshot. |
| desktop-shell | Modified | The Web View Reads The Engine State And Calls The Local API replaced by the delta's text (the status read goes through the client; a failed read is worded without the token; the page is built into `desktop/dist/`); one scenario added (The page words a failed read without the token). |
| desktop-packaging | Modified | The App Bundle Ships The Freeze Under Resources, Deep Ad-Hoc Signed replaced by the delta's text (the page is installed from the lockfile and built before bundling; a missing `index.html` fails the build); one scenario added (A page that did not build fails the build). |

The deltas' "(Previously: ...)" notes were not carried into the living specs.

### Heading Name-Match

Every `### Requirement:` and `#### Scenario:` heading of each delta was compared as an exact line against the living spec after the merge: desktop-client 18 headings (4 requirements, 14 scenarios), desktop-shell 3 (1 requirement, 2 scenarios), desktop-packaging 4 (1 requirement, 3 scenarios); 0 missing, no duplicate heading in any of the three living specs. No REMOVED or RENAMED headings.

## Requirement-vs-Code Check

Each requirement was read against the shipped files, not against the task list.

| Requirement | Result |
|-------------|--------|
| The Client Is Generated From The Committed OpenAPI Snapshot | Met. `scripts/schema.mjs` runs `openapiTS(snapshot, { defaultNonNullable: false })` and `astToString` for both modes; `--check` exits 1 naming `pnpm --dir desktop generate` when `client/schema.d.ts` is missing or differs. Every method calls `call(method, template)` with `template` a key of `paths`, its body typed from the operation's `requestBody` and its answer from its 200 or 202 response; `types.typecheck.ts` asserts each method's argument and answer equal `Schemas[...]`, tuples included. `errors.test.ts` compares `ERROR_CODES` with every `NAME: Final = "code"` in `src/openkos/localapi/_errors.py`, both directions. `plan-kind.typecheck.ts` switches on `plan.kind` with a `never` default. |
| The Client Takes Its Address And Token From The Engine State | Met. `createClient(ready)` resolves paths against the origin of `base_url` and throws a `TypeError` before any request unless the scheme is `http:`, the host `127.0.0.1`, the path `/v1` or `/v1/`, with no credentials, query or fragment. The token is set as `Authorization: Bearer` inside the `sender` closure and is not a property of the returned object. Errors are built from the status, the envelope, `Retry-After` and the request path only; a test checks the message, string, JSON and stack of an envelope error, a protocol error and an unreachable engine, and the client's string and JSON forms, for the token. |
| The Error Envelope Becomes A Typed Error | Met. `sender` raises `EngineUnreachable` when `fetch` rejects, and `ProtocolError` (with status and path) for a body that is not JSON or a failure without a well-typed envelope; `unwrap` raises `ApiError` with status, code, `retryable`, message and `retryAfterSeconds` (whole seconds, else null). `isBusy` is `workspace_busy` only; `isUnmergeRefusal` is `survivor_missing`, `not_merged`, `survivor_edited`. `retryWhileBusy` rethrows anything but busy at once, waits `Retry-After` (else 1 s), and rethrows the last busy error when the next wait would pass the deadline (5000 ms by default). |
| CI Holds The Client To The Snapshot | Met. The `desktop-client` job in `ci.yml` runs `pnpm install --frozen-lockfile`, `pnpm typecheck`, `pnpm test`, `pnpm check:schema` and `pnpm build` under the workflow's triggers (every pull request into `main` or `tracker/**`, every push to `main`), with no path filter. |
| The Web View Reads The Engine State And Calls The Local API (modified) | Met. `ui/main.ts` polls `engine_state` every second, words it with `describeEngine`, and once per ready launch (pid and base URL) calls `createClient(state).status()`; facts come from `statusFacts` and failures from `statusFailure`, which uses the error's class and the engine's fixed message only. Values are written with `textContent`. `tauri.conf.json`'s `frontendDist` is `../dist` and its CSP is unchanged. The live test, unchanged, still exercises the request the page sends. |
| The App Bundle Ships The Freeze Under Resources, Deep Ad-Hoc Signed (modified) | Met. `build-app.sh` removes `desktop/dist`, runs `pnpm install --frozen-lockfile` and `pnpm build` in `desktop/`, and exits 1 naming `desktop/dist/index.html` when it is missing, before freezing or bundling; the rest is unchanged. |

## Decisions Taken During Implementation

1. **No openapi-fetch (S2).** The plan first chose openapi-fetch as the runtime. Its `Readable`/`Writable` mapped types widen tuples to arrays, and the snapshot has three (`WorkspaceStatus.edges`, `UnmergeVersion.member_digests`, `Applied.merged`); the type-equality test failed on three methods (TS2344). The runtime became one hand-written `call` with no dependency, and the plan and ADR-0065 were revised in the plan PR before it merged, with openapi-fetch recorded as a rejected alternative.
2. **`defaultNonNullable: false` (S2).** With openapi-typescript's default every field that has a default is required, which would force callers to send `wait_seconds`, `include_confidential` and `discard_survivor_edits`. The cost, recorded in the design: six response fields the server always sends but the schema does not list as required are typed optional.
3. **Both protocol checks run where the request is sent (S2),** so a `ProtocolError` names the request's path; `unwrap` trusts the envelope `sender` already checked.
4. **`fillPath` is exported (S2)** so its refusal of a missing segment is tested; the public methods cannot reach it.

## Verification Record

- Strict TDD. S2: `check:schema` exited 1 on a missing file, a hand-edited file and a snapshot with a field added, before passing on the generated file; both test files failed to load and `tsc` reported TS2307 before the modules existed; `types.typecheck.ts` failed (three TS2344) under openapi-fetch before the hand-written call. S3: `ui/view.test.ts` failed to load before `view.ts`.
- Mutations on the exact guarded lines, each restored from a byte copy: S2 24, all killed (one, the JSON content type, survived its first run and got its own test); the two type-level tests each failed when a branch read another plan's field or a `case` was dropped. S3 5, all killed.
- Locally on Apple Silicon: 76 vitest tests, `tsc --noEmit`, `cargo fmt --check`, `cargo clippy --locked --all-targets -- -D warnings`, `cargo test --locked` (42); `desktop/scripts/build-app.sh` end to end, its smoke check passing against the engine inside the app and the release binary embedding `/assets/index-*.js` and `.css`. In CI, the bundle job's log shows the page built before the bundle and the smoke check passing.
- No Python changed: the Python gates ran in CI on every PR (all green); none was run locally.
- Not verified: the page rendering in the window (no screen access on the development machine), as in the shell stage.

## Follow-ups

- None filed. The review screen, guided setup and the updater are the roadmap's next stages, not defects of this change.
