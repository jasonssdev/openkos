# Design: Add Source Files Through `openkos.api` And The Local API

Inputs: `proposal.md`, ADR-0041, ADR-0059, ADR-0060, ADR-0061, ADR-0073
(Proposed). Paths are under `src/openkos` unless they start with `tests/`,
`docs/`, `desktop/` or `openspec/`.

## Current state

- `application/ingest_service.py::ingest_source(root, src, policy, *, ports,
  observer, confirm)` is the single-source use case. The CLI's `ingest` (one
  file or a batch) and the folder watch are its two adapters. Phase A writes
  nothing and may stop at the convergence decision (`probe_only`), which the
  watch reaches through `is_unchanged`, a probe that swallows every refusal.
- A person's ingest refuses a file whose bytes differ from the raw copy it
  matches (`RawImmutabilityRefused`); only the watch sets `version_changed`
  and imports such bytes as a new version (ADR-0041).
- When a raw copy lands, the service resolves every open `watch_refusal` row
  whose refused digest equals the landed bytes (`queue_resolution.
  resolve_watch_refusals`), whatever adapter ran it.
- The CLI's batch gate prices a run with `estimate_extraction_calls` (the
  ordinary path), and its docstring says so: the judge's retry, the re-ask,
  the window retries and the decision pass's re-ask depend on the reply. The
  watch admits by the same estimate and accounts by the count (`budget.py`).
- Judging's protocol (ADR-0059): a free quote with a token, a run against the
  token, `QuoteStale` and `RunRefused` returned, a ceiling client that refuses
  the call past the quote. The local API runs it as a job (`localapi/_runs.py`).
- The API may import `openkos.application.*` but not `openkos.extraction`,
  `openkos.fsio` or `openkos.state`; `openkos.localapi` imports only
  `openkos.api` among `openkos` modules.

## Decisions

### D1. The quote's number is a bound, computed by extraction itself

`extraction.concept.max_extraction_calls(text, *, union_judge,
transcript_decisions)` returns the most chat calls extracting `text` can make:
two per window (a window's call is retried once, #1053) or two below the
threshold (the union path's identical passes; the legacy path's retry of an
empty reply, #524), one re-ask, and on the union path one participant pass on
a meeting-shaped source, two per window for the decision pass when it is on,
and `JUDGE_ATTEMPTS` judge calls. Whether a source reads as a meeting depends
on its title as well as its text, and moves both the chunking threshold and the
meeting-only passes; the Source's title is not known before Phase A, so the
bound takes the larger of the two readings. Blank text is 0. It lives beside
`estimate_extraction_calls` so the thresholds are not re-derived elsewhere. A
test drives the real extractors with replies built to spend every retry and
asserts the count never passes the bound, over chunked and short sources, both
titles, both paths and the decision pass on and off. Recorded in ADR-0073.

### D2. The run enforces the bound per file, by degrading, not by failing

Each file gets its own counting client capped at its quoted share. The call
past the share raises `CallCeilingReached`, a `BackendError`, so the pipeline
treats it as it treats an unreachable backend: a window is skipped, the judge
keeps the union, and when an extraction pass fails the file is added as a
Source only, its extraction left as debt a later re-ingest retries. Judging
raises an assertion instead, because its bound is exact by construction; an
ingest whose quote said `unchanged` (0 calls) may find the workspace moved by
run time (the Source was forgotten), and a Source-only add with no spend is
the honest outcome there, not a crash. Recorded in ADR-0073.

### D3. The quote probes with the service's own Phase A

`ingest_service.probe_source` runs Phase A with `probe_only` and raises its
refusals instead of swallowing them (`is_unchanged` becomes a wrapper over it).
The quote maps them: `RawImmutabilityRefused` is `changed_after_import`,
`InconsistentWorkspace` is `inconsistent_workspace`, `SymlinkedDestination` is
`symlinked_destination`, `SourceNotReadable` is `not_found`, anything else is
`unreadable`. An unchanged file is `unchanged` at 0 calls. A readable file that
proceeds is then read as the service reads it (`fsio.read_source_text`): not
text or blank is `source_only` at 0 calls, as is a file below a confidential
workspace floor without `include_confidential` (the high-water rule never
lowers a Source below `default_sensitivity`); otherwise `extracts` at the D1
bound. The quote never builds a client it could call: the probe's client
factory raises.

### D4. Paths are checked before a byte is read

Each path must be absolute (the API never reads the process's working
directory), must `stat` (else `not_found`, or `unreadable` on any other
`OSError`), must resolve outside the workspace's `bundle/` and `.openkos/`
(`inside_workspace`), and must be a regular file after links are followed
(`not_a_file`). Only then is it hashed and probed. The same check runs again
for each file at run time, so an altered token cannot point the run at a
protected path. A file chosen twice (the same resolved path) is quoted once;
two different files with one name are both `duplicate_name`, because the
destination name is the basename (the CLI refuses a whole batch for this; a
person choosing files in an app is better served by refusing the two and
quoting the rest). `raw/` is not protected: re-adding a raw copy is the CLI's
"forget, then re-ingest" path.

### D5. The token binds the model, the host, the settings and the bytes

`IngestBinding` holds the extraction model (`resolve_task_model(cfg,
"extraction")`), the backend, the client's locality, the
`include_confidential` request bit and a digest of the settings that move the
bound (`union_judge`, `transcript_decisions`, `default_sensitivity`). Each
`IngestTokenFile` holds the path, the sha256 of the bytes the person
consented to and the file's bound. A run recomputes the binding and every
file's digest first and returns `IngestQuoteStale(changed)` naming `model`,
`backend`, `locality`, `settings` and `files` as they moved, before any call or
write. The workspace may move between quote and run without staleness, as
judging's work set may: a file that is now unchanged costs nothing, and a
refusal the service raises at run time is that file's result.

### D6. One file at a time, committed, then one refresh

The run calls `ingest_source` per file with `skip_confirmation` (the token is
the confirmation), `include_confidential` from the token and `version_changed`
off, a commit section that takes the workspace lock with the caller's bounded
wait, and no post-commit step. `stop` is observed before each file. A file's
result is `added`, `updated` (a regenerated re-ingest), `unchanged`,
`refused` with its reason, `interrupted` (`DriftDetected`: nothing written,
adding it again can work), `busy` (`WorkspaceBusyError`: nothing written) or
`failed` (`WriteFailed`: part landed). `busy` ends the run (`status` `busy`,
the rest `not_reached`): extracting files that cannot be committed would spend
the consent for nothing. After the loop, if anything was written, the derived
stores are refreshed once through `reindex_service` without `--force` (the
daemon's incremental refresh), fail-open: `indexes` is `refreshed`,
`not_needed` or `stale`.

### D7. Progress and the observer

`progress` hears `IngestProgress(index, total, path, phase, calls)` for
`reading`, `extracting` (when the service starts the model wait) and `writing`
(after the writes, before the commit) per file, then `refreshing` once. An
exception from it is recorded (`observer_failed`) and it is not called again,
as judging's observer guard does: a display must not cost paid work.

### D8. The API facade and wiring

`api/_ingest.py` exports `quote_ingest` and `run_ingest`; the value types are
re-exported from `application.ingest_consent`, as judging's are. `paths` is a
sequence of `str` or `os.PathLike`; one string or path passed alone is a
`ValueError` (a string is a sequence of characters). `_wiring.ingest_ports`
binds the extraction client (`default_chat_client(cfg, "extraction")`), the
commit core with a skipped commit reported as "the ingest was applied but not
committed: ...", `locked_commit_section`, and the refresh through
`ingest_consent.refresh_derived_stores` with `default_embedder`. `IngestRefused`
(an unreadable workspace) is under `OpenKosError`; `CallCeilingReached` never
reaches a caller and is not exported. The package docstring's statements on
what sends content and on stopping name adding files.

### D9. A refused file is quoted by its decision

A `watch_refusal` row names its file relative to the inbox (`inbox_path`), and
the app does not know where the inbox is. `quote_refused_file(workspace,
decision_key)` reads the open row (`curate_queue.open_row_by_key`), joins its
file to the configured `unattended.inbox` and quotes that path exactly as
`quote_ingest` does, so the run resolves the row through the service's own
resolution when the refused bytes land. No inbox configured is a quote whose
one file is refused `not_found`; a settled key is `Stale("row_resolved")`, an
unknown one `Stale("unknown_key")`, and a key of another kind a `ValueError`.
It reuses #1476's typed detail for nothing: the row is read here, so the action
works whether or not that detail has shipped.

### D10. The local API job

`localapi/_ingests.py` copies the judging run registry: a daemon thread per
run, admission at the service's first stop check (after the stale and refusal
checks), so a stale token or a missing one answers `POST /v1/ingest/runs`
itself (409 `quote_stale`, 422 `run_refused`) and leaves no job; one active run
(409 `ingest_active`, retryable); the newest 16 finished kept; a stop route;
shutdown sets every stop. The job is `{run_id, state, index, total, phase,
calls, outcome, warnings, error}`. `POST /v1/decisions/{key}/ingest-quote` takes `{include_confidential}` and
answers `Stale` as 409 `stale`. `POST /v1/ingest/quote` takes `{paths,
include_confidential}` with `paths` a list of 1 to 500 strings of at most 4096
characters each. `IngestRefused` maps to 503 `ingest_refused`. A busy
workspace is the outcome's `status` `busy`, never a failed poll. The adapter
reads no file itself: every path check is the API's.

## Sequence

```
client                localapi              api                 ingest_consent                 ingest_service
  | quote(paths)        |                     |                       |                              |
  |-------------------->| quote_ingest ------>| check paths, hash  -->| probe_source (Phase A, no    |
  |<--------------------| IngestQuote (token) |                       |   model) per file            |
  | start(token)        |                     |                       |                              |
  |-------------------->| thread: run_ingest ->| binding + digests ?  |                              |
  |<-- 202 job / 409 ---|  (admission = 1st stop check)              |                              |
  | poll                |                     | per file: stop? ----->| ingest_source(ceiling client)|
  |<-- job, progress ---|                     |                       |   commit section, autocommit |
  |                     |                     | once: refresh_derived_stores                         |
  |<-- finished outcome-|                     |                       |                              |
```

## ADR gate

D1 and D2 decide what a consented ingest promises and how it is held to it,
and D3/D5 decide that a person's changed file stays refused through the app as
on the CLI; a client ships against all of it: ADR-0073. D4 and D6-D10 follow
existing patterns and are recorded here.
