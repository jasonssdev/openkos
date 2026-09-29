# Proposal: Isolate a Failed Chunk from the Rest of an Extraction (#1053)

## Intent

`extraction/concept.py` chunks a source above `_CHUNK_THRESHOLD`/
`_MEETING_CHUNK_THRESHOLD` into one chat call per `_chunk_lines` window
(#454). Today an `OllamaError`-family exception on any ONE window has no
per-chunk `except`: it propagates unswallowed out of `extract_concept`/
`extract_concept_union` to `_ingest_single`'s single `except OllamaError`
handler, which degrades the WHOLE source to Source-only. A 12-window
transcript where 1 window is capped (`OllamaGenerationCapped`) yields zero
concepts, even though 11 windows were extracted, or could have been.

## Scope

### In Scope

- Retry a failed window once; if the retry also raises a `BackendError`
  (the backend-agnostic family `OllamaError` subclasses), skip that window
  and keep every other window's objects.
- Name the skipped window(s) on stderr and record the loss on the Source's
  `extraction_notice` frontmatter key via a new closed-vocabulary token,
  `chunk-extraction-partial`.
- Preserve today's behavior exactly when: every window's retry fails (whole
  source degrades to Source-only, unchanged stderr line); the source is
  below the chunking threshold (single call, unchanged); the backend is
  unreachable (`BackendUnavailable`, e.g. `OllamaUnavailable`) rather than
  merely erroring -- that propagates on the FIRST failure, never retried or
  skipped, because a down server will not answer the next window either.
- Delta spec for `ingestion` (this domain owns the degrade contract this
  change narrows).

### Out of Scope

- **No `lint`/`status` section for the new token.** Every other retryable
  `extraction_notice` token (`judge-selection-unavailable`/`-empty`) gets a
  dedicated `lint` finding and a `status` `needs_attention` fold. This
  token does not, for now: it is disclosed at ingest time on stderr (naming
  the exact chunk) and it self-heals on the very next plain re-ingest
  (`extraction_retry_due` now includes it), which the judge tokens also
  do but additionally get a persistent audit trail for. Adding a `lint`
  section is a reasonable follow-up but is a second, independent feature
  (new `LintFinding` kind, `LintReport` field, two more delta specs for
  `lint`/`status`) that this issue's own "Target behavior" does not ask
  for. Filed here as a disclosed scope decision, not silently dropped.
- **No change to `estimate_extraction_calls`/the batch cost gate.** A
  retry is spent only on failure, exactly like the existing `reask_runs`/
  `participant_capture_runs` optional calls, which the cost gate already
  excludes from its "ordinary path" estimate because they depend on what
  the model replies, not on the document. The gate already labels its
  number "(estimate; ...)"; nothing here makes an "exact" claim untrue.
- **No ADR.** This is a narrow resilience fix to an existing pipeline
  contract, not a new technology, pattern, interface, or hard-to-reverse
  trade-off.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `ingestion`: a chunked extraction's per-window failure contract changes
  from all-or-nothing to retry-once-then-isolate; a new `extraction_notice`
  vocabulary member records a partial chunk loss; `extraction_retry_due`
  treats it as retryable debt like the judge tokens.

## Approach

`_fan_out_window_lists` (shared by `extract_concept` and
`extract_concept_union`, the seam both entry points already share for
`_chunk_threshold_for`/`FAN_OUT_CONCURRENCY`) gains a
`_extract_window_or_skip` wrapper: it catches `openkos.llm.base.
BackendError` (the backend-agnostic base `OllamaError` already subclasses,
per `application/ingest.py`'s existing "adapters stay backend-agnostic"
rule), retries once, and on a second failure records the window's
1-indexed position and returns `[]` for it. `BackendUnavailable` (backend
unreachable) is excluded from retry/skip in both attempts and re-raises
immediately. If every window in a fan-out fails even after its retry, the
function re-raises the LAST window's exception unswallowed, preserving the
pre-existing whole-source degrade byte-for-byte.

`ExtractionReport` gains `skipped_chunks: tuple[int, ...] = ()`.
`application.ingest.stage_derived_objects` turns a non-empty tuple into the
new `okf.EXTRACTION_NOTICE_CHUNK_PARTIAL` token (checked first, since it is
the earliest condition the pipeline can detect). `cli/main.py` gains
`_chunk_skip_notice`, wired first into `_render_staged_derived_objects`'s
notice tuple, naming "chunk N of M" for every skipped position.
`extraction_retry_due` (the byte-identical-re-ingest gate) adds the new
token to its retryable-debt set, alongside the two judge tokens, on the
same "this is a transient backend condition, not a deterministic property
of the bytes" reasoning.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/extraction/concept.py` | Modified | `_fan_out_window_lists`/`_fan_out_windows` retry+skip; `ExtractionReport.skipped_chunks`; both entry points wire it through |
| `src/openkos/model/okf.py` | Modified | `chunk-extraction-partial` token + constant |
| `src/openkos/application/ingest.py` | Modified | `stage_derived_objects` notice check; `extraction_retry_due` set |
| `src/openkos/cli/main.py` | Modified | `_chunk_skip_notice`; batch-summary/lint-pointer prose updated for the sixth token |
| `docs/cli.md` | Modified | New paragraph; two stale "all windows propagate"/"four of five" passages corrected |
| `openspec/specs/ingestion/spec.md` | Modified | Delta (this change) |

## Rollback Plan

Pure code addition behind existing entry points; `git revert` is safe. Any
already-written `chunk-extraction-partial` marker is inert extra
frontmatter (OKF §4.1-tolerated) and self-clears on the Source's next
successful full re-ingest, exactly like every other `extraction_notice`
token.

## Success Criteria

- [x] A single chunk's `BackendError`-family failure, after one retry, is
      isolated: every other chunk's objects are kept.
- [x] Naming the skipped chunk(s) on stderr and in `extraction_notice`.
- [x] All-chunks-fail and unchunked-source behavior unchanged.
- [x] `BackendUnavailable` still degrades the whole source on first failure.
- [x] `uv run pytest`, `ruff`, `mypy` clean; branch coverage unaffected.
