# Proposal: Add Source Files Through `openkos.api` And The Local API

Tracking issue: #1496 (found in #1476). Governing decisions: ADR-0018
(application services), ADR-0021 (the synchronous core), ADR-0041 (the watch
imports changed bytes as a version; a person's ingest keeps the refusal),
ADR-0059 (judging is quoted, then run against the quote), ADR-0060 (the
versioned Python API), ADR-0061 (the local API adapter), ADR-0065 (the generated
TypeScript client). New decision record: ADR-0073 (Proposed, written with this
plan). Paths are under `src/openkos` unless they start with `tests/`, `docs/`,
`desktop/` or `openspec/`.

## Intent

A person who does not use a terminal must be able to add source files from the
app: it is the most basic thing the knowledge base needs. The terminal adds
them with `openkos ingest`, which asks its cost question on a TTY; the folder
watch adds them unattended under a budget. `openkos.api` has no ingest at all,
so the desktop app cannot add a file, and a `watch_refusal` row in To review
(#1476) has no action the app can offer: its remedy is an ingest.

## Scope

### In scope

- **`openkos.api.quote_ingest(workspace, paths, *, include_confidential)`**: a
  free quote for the files a person chose. It reads the files and the
  workspace, builds the extraction client only to read its model and locality,
  and returns, per file, what adding it would do (`extracts`, `source_only`,
  `unchanged`, `refused` with a reason) and an upper bound of chat calls, plus
  the total, the model, the backend, whether the host is this machine and the
  host as it may be shown, and the token a run needs.
- **An honest bound.** The CLI's batch gate prints the ordinary-path estimate
  (`estimate_extraction_calls`), which a retry, the judge's second attempt or
  the decision pass's re-ask can exceed. A new pure
  `extraction.concept.max_extraction_calls` counts every mechanism at its
  maximum, whatever title the Source takes, and the run enforces it per file.
- **`openkos.api.run_ingest(workspace, token, *, stop, progress, wait_seconds,
  on_warning)`**: adds the files the token covers through
  `ingest_service.ingest_source`, the use case the CLI and the watch share, each
  held to its quoted calls; a moved binding (model, backend, locality, the
  settings that move the bound, or any file's bytes) is `IngestQuoteStale`
  before any call; no token is `RunRefused`. A stop is observed between files.
  Each file is committed as the CLI commits it; the derived stores are
  refreshed once at the end.
- **The changed-after-import decision** (the issue's third point): a person's
  ingest keeps refusing a file whose bytes changed after a file of that name
  was added (raw/ is immutable; ADR-0041 kept the refusal for a person). The
  quote reports it as `changed_after_import`, and the remedy the app offers is
  a rename, not a retry.
- **A `watch_refusal` row resolves the CLI's way**: when a raw copy with the
  refused bytes lands, the service moves the row to applied. An over-budget
  refusal is resolved by adding its file; a changed-file refusal stays open.
- **Paths are validated before they are read**: absolute, existing, a regular
  file, and not inside the workspace's `bundle/` or `.openkos/`.
- **The local HTTP API**: `POST /v1/ingest/quote`; `POST /v1/ingest/runs`
  starts one pollable job (202), at most one active (409 `ingest_active`,
  retryable); `GET /v1/ingest/runs/{id}` and `POST /v1/ingest/runs/{id}/stop`.
  The regenerated OpenAPI snapshot.
- **The TypeScript client**: regenerated types and `quoteIngest`,
  `startIngest`, `ingestRun`, `stopIngest`, with tests.
- **Wording** for the future screen in `docs/decision-wording.md`.
- **A spec cleanup**: the model pull's "Poll to completion" scenario shares
  its name with the judging job's in `local-http-api`; renamed.

### Out of scope

- **The add-files screen.** Another change owns `desktop/ui/`.
- **The CLI.** No verb changes; its batch gate keeps printing the ordinary
  estimate. `docs/cli.md` does not change.
- **`--re-extract` and `--event-date` through the API**: additive later if a
  screen needs them.
- **Directory and glob expansion.** The app passes the files the person chose.
- **The typed `watch_refusal` detail** (the file and the reason code): #1476
  ships it; this change gives that row its action.

## Approach

A new application service, `application/ingest_consent.py`, owns the quote and
the run, the way `application/judging.py` owns judging's: the API module is a
thin facade and `_wiring` binds the effects (the extraction client, the git
commit core with the API's warning wording, the lock with a bounded wait, the
incremental reindex with the API's embedder). The quote prices each file by
running the service's own Phase A in probe mode (`ingest_service.probe_source`,
which raises the same refusals the run would), so the quote and the run cannot
disagree about what is refused or unchanged. The HTTP job follows the judging
run registry: its own daemon thread, admission at the run's first stop check,
so a stale quote answers the start request itself.

## Risks

- **A worst-case bound reads large.** With the default settings a short
  source is quoted at 6 calls and usually spends 3; the quote states the most. The wording says "at most" and "usually fewer".
- **A Source-only add when the ceiling binds.** The bound is proven by test,
  so the ceiling should never bind; if a future extraction mechanism adds a
  call without updating the bound, the file is added as a Source only (the
  degrade an unreachable backend causes) rather than overspending, and the
  bound's test fails.
- **Surface growth.** Every new dataclass field freezes within v1.
- **Concurrent changes** to `api/__init__.py`, the OpenAPI snapshot and the
  generated client (#1476, the desktop changes): resolved by keeping both
  sides and regenerating.
