## Exploration: local-http-api (MVP 6 stage: FastAPI adapter over openkos.api)

### Current State
- openkos.api (src/openkos/api/__init__.py:112 API_VERSION=1) exposes 8 functions: status, pending_report, list_decisions, preview_decision, apply_decision, quote_judging, run_judging, refresh_decision_queue, plus Workspace handle (_workspace.py:61). Sync, no threads/loop/server (docstring :6). Owner-local, no disclosure gate (:11).
- Nothing outside openkos.api may import it: ADR-0060 :32, spec stable-python-api:163, guard tests/unit/api/test_api_guards.py:189. The HTTP adapter is the first importer -> guard + spec MUST be amended.
- mcp/ pattern (src/openkos/mcp/server.py): hand-rolled; run_in_worker (:273) = one daemon thread per call + Semaphore(4) + deadline; cancellation = abandonment; error table subclass-first (:96); lazily imported inside the CLI verb (cli/main.py:15924), pinned by tests/unit/cli/test_mcp_cmd.py:139.
- ADR-0058 (Proposed): 127.0.0.1 random port; per-launch bearer token over stdin (never URL/argv/file); 401 missing / 403 wrong; Host must be loopback+engine port; Origin allowlist; same checks on every route.
- Spike (openkos-spike-http/RESULTS.md, outside the repo): FastAPI+uvicorn frozen OK, +13 dists/9.8MB, +8.2MB frozen, ~0.11s import, 0.32s first response; middleware order Host->Origin(if present)->token; Decision dataclass tree validated via TypeAdapter non-frozen only; live endpoints returned [] (populated HTTP output NOT exercised); OpenAPI/TS client NOT measured; SSE only counted 5 events.

### 1. Surface -> endpoints, serializability
1:1 candidates (all take Workspace; handle held by server, workspace fixed at launch like `mcp --workspace`):
- GET status -> WorkspaceStatus (_reads.py:61). Fields: Path (str OK), Mapping[str,int] via MappingProxyType (pydantic Mapping validation/serialization of MappingProxyType UNVERIFIED), edges tuple[int,int]|None (prefixItems in OpenAPI 3.1).
- GET pending -> PendingReport (_reads.py:109): plain tuples/str; OK.
- GET decisions -> tuple[Decision,...] (_decisions.py:72): Literal, StrEnum Action, tuple, union detail IdentityDetail|RelationDetail|TierDetail|None; OK, union is NOT discriminated (TS gets structural union).
- POST decisions/{key}/preview (key, Action) -> DecisionPreview | NotApplied | Stale; plan is an undiscriminated union of 4 dataclasses.
- POST apply: needs DecisionVersion round-trip (3 str fields, curate_decisions.py:67). DecisionVersion.token() is a one-way sha256 ("parsing back deferred to the API stage", :82) -> the HTTP stage must define the wire form: echo the 3 fields as JSON (stateless, matches "stateless caller" doc).
- POST refresh -> QueueRefresh (_refresh.py:38): plain; OK.
- POST judging/quote (kind, include_confidential, include_uncertain) -> JudgingQuote (judging.py:249): nested frozen dataclasses incl. QuoteToken with tuple[WorkItem...] and tuple[tuple[str,str],...]; OK as response. QuoteToken.digest() "Nothing parses it back" (judging.py:203) -> run needs either the full token echoed back in the body (stateless) or a server-side quote registry keyed by digest.
- POST judging run -> RunResult = JudgingOutcome | QuoteStale | RunRefused. JudgingOutcome.failure is `BackendError | None` (judging.py:1036) = an exception instance: NOT servable as response_model (pydantic arbitrary-type schema error at app build). Needs an HTTP DTO (failure code + fixed message, like mcp's fixed messages, server.py:96).

Needs adaptation / flagged: JudgingOutcome.failure; unions without discriminators (DecisionPreview.plan, ApplyOutcome, RunResult, Decision.detail) -> consider additive `kind` discriminator at the HTTP layer; MappingProxyType fields; FastAPI emits "-Input/-Output" duplicate schema names unless separate_input_output_schemas=False (affects TS names). Nothing here was executed; slice 1 must test populated models over HTTP.

### 2. Error mapping (hierarchy rooted at OpenKosError, errors.py:9)
Proposed table (subclass-first, fixed messages, body {code, retryable, message}):
- WorkspaceBusyError (lock.py:66) -> 503 + Retry-After, retryable (roadmap: "busy -> try again"); WorkspaceLockUnavailableError (:74) -> 500 non-retryable.
- Returned values: Stale / QuoteStale -> 409; NotApplied -> 409 (not_offered/not_mergeable) or 422; RunRefused(no_quote_token) -> 422. (Open decision: value-as-200-tagged-union vs status codes.)
- WriteRefused family (write_gate.py:42): DriftDetected (:78, "the one refusal a caller may retry") -> 409 retryable; Refused (:54) -> 422; WriteFailed(Refused) (:58) -> 500; ConfirmationDeclined/Unavailable should be unreachable via API.
- QueueUnavailableError, QueueRefreshRefused, JudgingRefused, WorkspaceUnreadable, WorkspaceNotFound -> 503/500 with distinct codes (workspace problems mostly fail launch).
- BackendUnavailable (llm/base.py:159) -> 503 retryable (mcp row: ollama_unavailable); BackendModelNotFound (:171) -> 424 or 409 non-retryable; BackendGenerationCapped (:182) / BackendHostLocality / BackendError -> 502.
- ValueError from wait_seconds/kind -> 422 (request validation should pre-empt). Unknown exception -> 500 generic fixed message, logged.
- Never str(exc) in the body except WriteRefused.message, which is documented user-facing text (owner-local).

### 3. Long-running operations
- Only judging is model-bound (run_judging; quote_judging builds no client call but scans candidates). run_judging has JudgingObserver.progress/chunk_persisted callbacks (judging.py:279) invoked from the worker thread and a cooperative StopToken (between chunks, waits for chunk in flight: api docstring :16). Per-chunk persistence means abandonment/crash loses at most the chunk in flight.
- Roadmap (docs/roadmap.md:232) owes: write operations, cancellation, busy->try again, loopback auth. It does NOT name SSE or progress; ADR-0057 progress is Ollama model pull (setup), out of this stage (ingest/query/doctor/setup not in api v1).
- Constraint: browser EventSource cannot send an Authorization header, and ADR-0058 forbids token-in-URL -> SSE must be consumed by fetch streaming, not EventSource; the spike's SSE check used a non-browser client and only counted events.
- Recommended shape: judging run is a job resource (single active run; second -> 409): POST /judging/runs {token} -> 202 {run_id}; GET /judging/runs/{id} (state, index/total, outcome DTO) polled; POST /judging/runs/{id}/stop sets StopToken. SSE as an additive later stage. Run executes on a dedicated daemon thread (not the anyio pool) so a minutes-long call never starves the 40-thread pool. Short ops: plain `def` endpoints (anyio threadpool) = worker-per-operation in spirit; the ADR must state this deviates from mcp's literal thread-per-call + Semaphore(4) (server.py:69).

### 4. Launch
- New CLI verb needed (docs/cli.md lists mcp at :940 as stdio-only, "no port to open"; the local API is listed as planned at :1284). Candidate `openkos serve --workspace PATH` (name = open decision). Lazy-import fastapi/uvicorn inside the verb body, mirroring cli/main.py:15924 and test_mcp_cmd.py:139 (add an equivalent "fastapi not imported at CLI startup" subprocess test). Update cli.md; add to the read-only/lock classification at cli/main.py:~367 as appropriate.
- Token: shell generates, writes one line to engine stdin; engine reads before binding; never argv/env/file/URL (ADR-0058 :45). Port: bind socket 127.0.0.1:0 itself, then pass sockets=[sock] to uvicorn.Server so the Host middleware knows the real port; announce readiness + port as one JSON line on stdout (stdout otherwise reserved, mirrors mcp claim_stdio transport.py:49), logs to stderr/file.
- Lifecycle: stdin EOF = shell gone -> graceful shutdown (mirrors mcp "closing stdin exits", spec mcp:756) = no orphan on force-quit (roadmap :228 pass/fail criterion). SIGTERM -> set StopToken of any active run, then exit; run waits for chunk in flight.
- Lock: API takes the workspace lock only inside commit sections with wait_seconds in [0, MAX_WAIT_SECONDS=3600] (_wiring.py:44). HTTP must choose a default/ceiling (small, e.g. 0-5s) and not let a client request 3600s; WorkspaceBusyError -> 503. Reads take no lock. Daemon coexists as a separate process (short lock phases, cli.md:707).
- CORS: Tauri webview origin (tauri://localhost) is cross-origin to 127.0.0.1:port; preflight OPTIONS carries no Authorization, so CORSMiddleware must sit outside the token check (Host check still first). NOT covered by ADR-0058 test table nor the spike.

### 5. Packaging / docs impact
- pyproject.toml:44 deps = 6 direct; optional extra exists for watch (:58). FastAPI = +13 dists. Core dep vs optional extra (`api`/`serve`) is a decision: extra keeps `pip install openkos` lean; but the frozen engine needs it, and CI `uv sync --locked` (ci.yml:59,100,140) installs only the dev group -> fastapi, uvicorn, httpx (TestClient) must go in the dev group so tests/mypy (strict, files=src+tests)/90% coverage run; wheel smoke (ci.yml:208) runs `openkos --help` without the extra -> must still pass and the verb must fail with a clear "install openkos[api]" remedy when the extra is missing.
- Text to update: AGENTS.md:43 ("pydantic and instructor are deliberately not dependencies" -> scope to LLM extraction/core), openspec/config.yaml:5 (context line), docs/tech_stack.md:66,83,107 (Pydantic not adopted; "no web framework is a dependency yet"), docs/architecture.md:238-243 (HTTP adapter bullet -> shipped package), docs/cli.md (new verb; :1284 sentence), docs/roadmap.md MVP 6 status. DO NOT edit ADR-0021:169-172 or ADR-0027 (append-only history). fastapi hard-requires opentelemetry-api (spike) -> note in ADR. No test pins the dependency list.
- Guards to change: tests/unit/api/test_api_guards.py:189 (allow the adapter package as sole importer) + spec stable-python-api:163 and :192 scenario (MODIFIED); mcp layering test bars mcp from api internals (unchanged). Adapter must NOT import openkos.mcp.gate unless the gate is wanted (see open decision on disclosure).
- Surface snapshot precedent: tests/unit/api/api_surface.json + test_api_surface.py + surface.py -> same technique fits a committed openapi.json snapshot.

### 6. OpenAPI -> TypeScript
- Verifiable in this stage in two layers: (a) in pytest, no Node: app.openapi() equals a committed openapi.json snapshot (same pattern as api_surface.json), with a populated-model round-trip test; (b) generating TS (openapi-typescript or @hey-api/openapi-ts) + `tsc --noEmit` needs Node, which the repo has none of; the app repo layout (monorepo vs separate) is undecided. Recommend: (a) in the stage; (b) as a one-off measured spike recorded in the design (not CI), CI job deferred to the desktop-shell stage. Known risk spots: undiscriminated unions, prefixItems tuples, -Input/-Output names, StrEnum.

### 7. Specs / ADRs
- New spec domain `local-http-api` (transport, auth middleware, endpoints, error mapping, job semantics, launch protocol). MODIFIED: stable-python-api (importers guard :163/:192; note the adapter is the first client). No mcp delta (distinct adapter); the verb can live in the local-http-api spec (cli-version is the only cli* domain).
- ADRs: next number 0061. 0061 = "The local API is a FastAPI + uvicorn ASGI adapter over openkos.api" (framework, pydantic as first dep + scope of the old rule, optional extra vs core, threadpool vs thread-per-op, ASGI middleware for ADR-0058, stdin-token/port-0/ready-line launch). Possibly 0062 only if the job/progress model (polling vs SSE) is contested. ADR-0058 stays Proposed until this lands. Status must be Proposed at write time (config.yaml rules).

### 8. Slicing (auto-chain, each <=400 authored lines; uv.lock generated, excluded)
- S1 docs/decision: ADR-0061, deps in pyproject (+dev group), AGENTS/config/tech_stack text, local-http-api spec skeleton.
- S2 adapter core: package, app factory, ASGI Host/Origin/token + CORS middleware, error table, /health + GET status/pending/decisions with response_model, populated-model tests, import-guard amendment (~350).
- S3 writes: preview/apply/refresh endpoints, DecisionVersion wire form, wait_seconds policy, 409/503 mapping tests.
- S4 judging: quote + run job registry + stop + poll (+ observer), DTO for JudgingOutcome, thread tests.
- S5 launch: CLI verb, stdin token, port 0, ready line, stdin-EOF shutdown, lazy-import test, subprocess integration test, docs/cli.md, wheel-smoke-without-extra test.
- S6 contract: committed openapi.json snapshot test + measured TS-client check recorded in design/verify; docs/roadmap.md status.
- S2 may need splitting (middleware vs endpoints) if >400.

### Approaches
- A. Thin FastAPI adapter, stateless tokens echoed by client, judging as polled job, extra `api` (RECOMMENDED). Pros: matches ADR-0060 "stateless caller", restart-safe, no SSE/EventSource-auth problem, lean default install. Cons: client must echo QuoteToken (big payload), two-step UI polling.
- B. Same but SSE for progress now. Pros: live progress; spike proved server side. Cons: EventSource cannot auth; fetch-stream client code; OpenAPI poor for SSE; more tests.
- C. Server-side quote/version registry. Pros: small wire. Cons: in-memory state, lost on restart, contradicts stateless design.
- D. FastAPI as core dependency. Pros: simple CI/frozen build. Cons: every pip user pays 13 dists/9.8MB for a desktop-only adapter.

### Risks
- Wire form of QuoteToken/DecisionVersion is unspecified by the facade (tokens are one-way digests); a wrong choice is a breaking change after API_VERSION freeze.
- JudgingOutcome.failure is an exception object: response_model generation would fail at app build.
- Populated-model serialization (MappingProxyType, tuple[int,int], unions) never exercised over HTTP; OpenAPI/TS usability unmeasured.
- Threadpool (anyio, 40) vs ADR-0021 thread-per-operation deviation; a stuck model call cannot be preempted (StopToken only between chunks) -> shutdown must not wait indefinitely; daemon threads.
- CORS preflight vs token middleware ordering untested; EventSource cannot carry the bearer.
- Disclosure: ADR-0058 says the gate applies to every response, but ADR-0058:48 and the facade doc say decision payloads are owner-local; WriteRefused.message, IdentityDetail.rationale, PendingRow.subject, recent_activity may echo confidential-derived text. Must be an explicit decision, not an accident.
- ADR-0058 still Proposed and its "Not measured" items (real DNS rebinding, Windows named pipes) remain.
- Exploration claims come from reading code; nothing was executed.

### Open product decisions
1. Package/module name for the adapter (openkos/http is confusable with stdlib; alternatives: openkos/localapi, openkos/server) and CLI verb name (`serve`? `api`?).
2. Optional extra (`openkos[api]`) vs core dependency.
3. Judging progress: polled job now + SSE later (recommended) vs SSE now.
4. Token wire form: client echoes full QuoteToken/DecisionVersion (stateless, recommended) vs server-held registry keyed by digest.
5. Outcomes-as-values on the wire: HTTP status (409/422) vs always-200 tagged union.
6. Disclosure gate in this stage: owner-local pass-through for the review-queue surface (per ADR-0058:48) with the gate deferred to the first ask/query endpoint, vs gate everything now.
7. Lock-wait policy for writes: default wait_seconds and a ceiling, vs surface 503 immediately.
8. API URL versioning: /v1 prefix tied to API_VERSION, vs unversioned.
9. TS client: in CI this stage (needs Node tooling and a decision on where the app lives) vs snapshot-only now and a measured one-off.
10. Whether CORS preflight handling is recorded in ADR-0058 (Proposed) or ADR-0061.
