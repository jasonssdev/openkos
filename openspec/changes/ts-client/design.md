# Design: A TypeScript Client For The Local API, With Node CI

Inputs: `proposal.md`, `specs/desktop-client/spec.md` (a new domain),
`specs/desktop-shell/spec.md` and `specs/desktop-packaging/spec.md` (deltas),
ADR-0065 (written with this design), ADR-0061, ADR-0058, ADR-0055, the archived
`2026-10-16-local-http-api`, `identity-groups-decision`, `unmerge-api`,
`desktop-shell` and `engine-freeze` changes, and measurements taken while
planning (Node 24.18.0, pnpm 11.13.1, TypeScript 5.9.3, openapi-typescript
7.13.0, openapi-fetch 0.17.0 (measured, then rejected; see D2), vitest 4.1.12,
Vite 8.3.4).

## D1 Where it lives: `desktop/` is the package, the page is built into `desktop/dist/`

```
desktop/
├── package.json · pnpm-lock.yaml · tsconfig.json · vite.config.ts · .node-version
├── scripts/schema.mjs        generate or check client/schema.d.ts from the snapshot
├── client/                   the API client (no DOM)
│   ├── schema.d.ts           GENERATED from tests/unit/localapi/openapi.json, committed
│   ├── index.ts              createClient(engine state), one typed method per operation
│   ├── errors.ts             ApiError, ProtocolError, EngineUnreachable, the code list
│   ├── engine.ts             the EngineState union the shell's `engine_state` returns
│   └── *.test.ts             vitest, against a mocked fetch
├── ui/                       the page's source (Vite root)
│   ├── index.html · style.css · tokens.css
│   ├── main.ts               the status page, over the client
│   └── *.test.ts
├── dist/                     BUILT, ignored: Tauri's frontendDist
├── engine/ · scripts/ · src-tauri/   unchanged
└── node_modules/             ignored
```

The package root is `desktop/`, not `desktop/ui/`, because Tauri embeds every
file under `frontendDist`: the built page must sit in a directory holding
nothing else, and `node_modules` must never be under it. `tauri.conf.json`'s
`frontendDist` becomes `../dist`. The client stays free of the DOM, so the next
screen imports it the same way the status page does.

Measured: `tauri-codegen` 2.7 panics when `frontendDist` is missing ("this path
doesn't exist"), so every `cargo` build of the shell needs `pnpm build` first.
`devUrl` and `beforeDevCommand` are not set: `cargo run` keeps loading the
embedded page, and the dev loop is `pnpm --dir desktop build` then `cargo run`
(or `pnpm --dir desktop build --watch` beside it).

## D2 The generator: openapi-typescript for types, a hand-written typed call

openapi-typescript 7.13 turns the snapshot into `paths`, `components` and
`operations` types (2,171 lines, no warnings). It is types only, so the generated
file is a pure function of the snapshot and is committed for review. Its peer
range is TypeScript 5 (it builds its output with the TypeScript compiler API),
so the package pins TypeScript 5.9, not the native TypeScript 7.

The runtime is one hand-written function, `call(method, template, {params,
query, body})`, about forty lines with no dependency. `template` is a key of
the generated `paths` and `method` one of its operations, so a path the
snapshot no longer has fails `tsc`; the body is typed from that operation's
`requestBody` and the answer from its 200 or 202 response. Each public method
(`status()`, `applyDecision(key, body)`, ...) is one line over `call`.

openapi-fetch 0.17, the same project's runtime, was the first choice and was
measured before it was dropped: its `Readable`/`Writable` mapped types widen
every tuple to an array, and the snapshot has three (`prefixItems`):
`WorkspaceStatus.edges`, `UnmergeVersion.member_digests` and `Applied.merged`.
A type-level test that every method returns exactly `Schemas[...]` failed on
three methods under openapi-fetch and passes with the hand-written call. With
the widened types, a version a screen echoes back would be checked against
`string[][]` instead of `[string, string][]`. Other alternatives are in
ADR-0065: runtime SDK generators, JSDoc over plain JavaScript.

Generation runs with `defaultNonNullable: false`, so the types follow the
schema's `required` lists literally. With the default (`true`), every field that
has a default becomes required, so a caller would have to send `wait_seconds`,
`include_confidential` and `discard_survivor_edits` on every request. The cost:
six response fields the server always sends but the schema does not list as
required (`Applied.committed_confidential`, `merged` and `kept_distinct`,
`Unmerged.committed_confidential`, `KindRefresh.ambiguous`,
`UnmergeVersion.discard_survivor_edits`) are typed
optional, and a reader supplies their default.

The plan union narrows: `switch (preview.plan.kind)` over `merge`, `relate`,
`set_tier` and `keep_distinct`, with `never` in the default branch, compiles
under `--strict` (measured on the snapshot). A type-level test pins it.

## D3 The drift check

Three guards, each failing on a different kind of drift:

1. **Generated types.** `scripts/schema.mjs` runs openapi-typescript's Node API
   (`openapiTS` then `astToString`) on the snapshot. `pnpm generate` writes
   `client/schema.d.ts`; `pnpm check:schema` compares the result with the
   committed file and fails, naming `pnpm --dir desktop generate`, when they
   differ. One function serves both, so they cannot disagree. A Python change
   that regenerates the snapshot without the client fails the Node job.
2. **Type-level use.** `tsc --noEmit` over the client and the page: a renamed or
   removed field that either uses fails to compile against the regenerated
   types.
3. **Error codes.** The envelope's `code` is a plain string in the schema, so
   the codes are not in the snapshot. The client's `ERROR_CODES` list is compared
   by a vitest test with every `NAME: Final = "code"` constant in
   `src/openkos/localapi/_errors.py`, both directions. The engine's table is the
   source; the test reads it as text and runs no Python.

## D4 Errors

Every non-2xx response is `{code, retryable, message}` (`local-http-api`). The
client raises:

- `ApiError` for a response carrying that envelope: `status`, `code`,
  `retryable`, the engine's `message`, and `retryAfterSeconds` from a
  `Retry-After` header when present. `code` is typed `ErrorCode | (string & {})`:
  known codes autocomplete and narrow, and a code from a newer engine is kept
  rather than lost.
- `ProtocolError` for a response that is not 2xx and does not carry the
  envelope, or a 2xx whose body is not JSON. Both are checked where the request
  is sent, so the error names the request's path.
- `EngineUnreachable` when `fetch` itself rejects (the engine stopped, the port
  closed).

Predicates name the cases the screens act on: `isBusy` (`workspace_busy`, the
wording brief's "try again"), `isTryAgain` (any `retryable` envelope),
`isStale` (`stale`, `write_drift`), `isUnmergeRefusal` (`survivor_missing`,
`not_merged`, `survivor_edited`). `request_too_large`, `invalid_selection`,
`merge_group_interrupted` and `unmerge_interrupted` are typed codes a screen
switches on.

`retryWhileBusy(call, {deadlineMs = 5000})` repeats a call only while it fails
with `workspace_busy`, waiting `Retry-After` seconds (default 1) between tries
and never past the deadline, then rethrows the last busy error. It retries
nothing else: `write_drift` and `record_failed` are retryable but each needs the
person (the brief reloads or asks), and an automatic retry of an apply after
anything but a lock refusal could act on a version the person never saw. A busy
refusal writes nothing (`WorkspaceBusyError` is raised before any write), so
repeating it is safe.

## D5 The token stays in one closure

The token reaches the client once, from a `ready` engine state, and lives only
in the closure of the function that sends each request and sets
`Authorization`. It is not a
property of the client, so `JSON.stringify`, `String` and a console dump of the
client do not show it. Errors are built from the status, the envelope and the
URL path, never from the request or its headers. A test asserts the token is
absent from every error's `message`, `String(error)` and `JSON.stringify` of the
client and of each error. The page never logs.

## D6 The base URL

`engine_state.ready.base_url` is `http://127.0.0.1:<port>/v1`, and the schema's
paths carry the `/v1` prefix. The client resolves each path template against the URL's
origin and refuses (a `TypeError`, before any request) a base URL that is not
`http://127.0.0.1` with the path `/v1`, so a misconfigured state cannot send the
token anywhere else.

## D7 The page

`ui/main.ts` keeps the page's behaviour: poll `engine_state` every second, show
the engine's state in English, read the status once per ready engine (keyed on
pid and base URL), write every value with `textContent`. What changes is that it
builds a client from the ready state and calls `client.status()`. The pure
parts (`describeEngine(state)`, `statusFacts(status)`, `statusFailure(error)`)
move to `ui/view.ts` and are unit-tested; `statusFailure` words the three
error classes without exposing a code or the token.

The CSP is unchanged: Vite emits one module script and one stylesheet under
`assets/`, both `'self'`.

## D8 CI

A new job, `desktop client (typecheck, test, schema, build)`, on
`ubuntu-latest`: checkout, `pnpm/action-setup` and `actions/setup-node` (pinned
by SHA, Node from `desktop/.node-version`, the pnpm store cached on the
lockfile), then `pnpm install --frozen-lockfile`, `pnpm typecheck`, `pnpm test`,
`pnpm check:schema`, `pnpm build`. It runs beside the other jobs and takes
seconds.

The `desktop` job (Linux, `cargo`) and `desktop-bundle` (macOS) set up the same
Node and pnpm. `desktop` runs `pnpm install --frozen-lockfile` and `pnpm build`
before `cargo fmt`; `desktop-bundle` gets them through `build-app.sh`, which now
installs and builds the page before `cargo tauri build` and fails if
`desktop/dist/index.html` is missing.

## D9 Versions

`package.json` pins exact versions, all of them development dependencies; `packageManager` is `pnpm@11.13.1` and
`engines.node` is `>=24`. TypeScript 5.9.3 (openapi-typescript's peer range is
5.x), openapi-typescript 7.13.0, vitest 4.1.12 (the 5.x line was a day old
when planned), Vite 8.3.4, `@types/node` 24. No runtime dependency.

## ADR Gate

The change adds a second toolchain and a build step to the desktop app and
fixes how the app reaches the API: a technology and a pattern, hard to reverse
once the review screen is written on it. ADR-0065 records it, Proposed, accepted
at archive.

## Slices

| Slice | Content | Forecast (authored; lockfile and `schema.d.ts` excluded) |
|-------|---------|-----------------------------------------------------------|
| S1 | This plan and ADR-0065 | ~600 (docs) |
| S2 | `desktop/` package, the client, its tests, the drift check, the Node CI job | ~900, more than half tests (`size:exception`) |
| S3 | The page on Vite and the client, `frontendDist`, `build-app.sh`, the shell jobs, architecture and contributing docs | ~350 |
| S4 | CHANGELOG, roadmap, spec merge, ADR-0065 Accepted, archive | ~250 |

Delivery: stacked to `main`, each slice branched from `main` after the previous
merge.

## Testing

| Layer | What | How |
|-------|------|-----|
| Client unit | every method's path, verb, body and auth header; envelope to `ApiError`; non-envelope to `ProtocolError`; network rejection to `EngineUnreachable`; `Retry-After`; `retryWhileBusy` bounds; the token absent from errors and serialisations; base URL refusal | vitest with an injected `fetch` |
| Type level | plan narrowing on `kind` is exhaustive; every method takes and returns exactly the schema's types | `tsc --noEmit` over two test files |
| Contract | committed types equal the snapshot's; `ERROR_CODES` equals the engine's table | `pnpm check:schema`; vitest reading `_errors.py` |
| Page | state wording, status facts, failure wording | vitest on `ui/view.ts` |
| Shell | unchanged Rust tests and the live engine test, compiled over the built page | the `desktop` job |
| Bundle | the app embeds the built page | `desktop-bundle` job via `build-app.sh` |

Not verified by automation: the page rendering in the window (no screen access,
as in the shell stage). The request it sends is the one the live test already
exercises.
