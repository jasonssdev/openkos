# Proposal: Reindex Embedding Resilience

## Intent

With the shipped default embedder (`qwen3-embedding:0.6b`), `openkos reindex` is
unusable on realistic bundles: the model raises non-deterministic EOF crashes
with no stable token threshold (measurement #1523 proved this). `reindex` embeds
one vector per whole doc in a single batch (`state/reindex.py:223`) under an
atomic single end-of-run commit, so ONE embed failure aborts the entire run.
Separately, `query` embeds the question through the same flaky path, and
`_dense_search` (`retrieval/answer.py:236`) catches only
`(VecUnavailable, sqlite3.Error)` — an `OllamaError` (plain `Exception`,
`ollama.py:24`) escapes and crashes the whole query (exit 1) instead of degrading
to FTS-only. This change restores a working out-of-box experience via three
combined moves; measurement shows no single move suffices.

## Scope

### In Scope
- **Resilient reindex embedding**: retry-with-backoff + per-doc/sub-batch
  isolation so one embed EOF does not abort the run; reconciled with the atomic
  single-commit contract and the model-tag `skipped == 0` self-heal rule.
- **Robust default embedder**: change `DEFAULT_EMBEDDING_MODEL` from
  `qwen3-embedding:0.6b` to `bge-m3` (measured 0/10 fails 8k–100k chars; 1024-dim,
  satisfies the `EMBED_DIM=1024` contract; migrates via the existing model-tag
  re-embed gate; requires user `ollama pull bge-m3`).
- **Query-side resilience**: `_dense_search` also degrades to `dense_degraded=True`
  when the question embed raises `OllamaError`.

### Out of Scope (deferred future changes)
- **Chunking** long docs into multiple vectors — a retrieval-QUALITY concern, not
  the crash fix.
- **Progress/feedback UX** for ingest/reindex/query — no progress shown while
  Ollama works.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `reindex-command`: partial-progress embedding with retry/sub-batch isolation,
  reconciled with atomic commit and the `skipped == 0` tag self-heal.
- `query-answer`: `_dense_search` degrades on `OllamaError`, not just
  `(VecUnavailable, sqlite3.Error)`.
- `llm-client`: default embedding model becomes `bge-m3`; retry-with-backoff on
  transient embed errors (final home TBD — see Approach).

## Approach

Three coordinated moves. Key design questions for sdd-design/spec:
1. **Retry home**: inside `OllamaClient.embed` (reusable, transparent) vs. in
   `reindex` (keeps client thin, run-aware). Tradeoff to resolve.
2. **Partial-progress vs. atomic commit**: how a failed doc reconciles with the
   single end-of-run commit and the `skipped == 0` rule — a failed embed must
   count as `skipped` so the model tag is NOT prematurely persisted
   (`reindex.py:254`), stranding stale vectors.
3. **Sub-batch granularity vs. tests**: `test_reindex.py` asserts
   `embedder.call_count == 1` (lines 149/169/565/695); per-doc/sub-batch embedding
   changes call granularity — spec/design must update these assertions.
4. **Migration cost**: bge-m3 default triggers one forced full re-embed via the
   model-tag gate on existing stores.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/state/reindex.py` (~223,254,259) | Modified | Retry + per-doc/sub-batch isolation; failed doc → `skipped`; reconcile with atomic commit |
| `src/openkos/llm/ollama.py` (~120-180) | Modified | Optional retry-with-backoff on transient embed errors |
| `src/openkos/config.py` (:23) | Modified | `DEFAULT_EMBEDDING_MODEL` → `bge-m3` |
| `src/openkos/retrieval/answer.py` (:236) | Modified | `_dense_search` degrades on `OllamaError` |
| Tests | Modified | `test_reindex.py` call_count asserts; `test_config.py:513`, `test_reindex_cmd.py:152` hardcode old default |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Retry does NOT eliminate qwen3 crashes | High | Robust default (bge-m3) is the reliability guarantee; retry only reduces transient loss |
| Premature model-tag persist strands stale vectors on partial failure | Med | Failed embed counts as `skipped`; tag persists only when `skipped == 0` |
| Sub-batch change breaks `call_count == 1` tests | High | Flagged for spec/design; update assertions deliberately |
| Default change breaks pinned-default tests | High | Update `test_config.py:513`, `test_reindex_cmd.py:152`, doctor tests |
| Existing stores incur one forced full re-embed | Med | Expected, self-heals via model-tag gate; document it |

## Rollback Plan

Revert the source edits. The default-model change is a one-line constant; a store
re-embedded under bge-m3 stays valid, and reverting to qwen3 re-triggers the
model-tag gate (one re-embed back). No destructive schema migration.

## Dependencies

- User must `ollama pull bge-m3` (surfaced by `doctor`).
- Builds on the MVP-2 model-tag re-embed gate (follow-up #5) and reindex
  lock-handling (#105), both archived.

## Success Criteria

- [ ] `reindex` completes on a realistic bundle without aborting on a single embed
      failure; failed docs are skipped, not fatal.
- [ ] Default embedder is `bge-m3`; existing stores self-heal in one re-embed.
- [ ] `query` degrades to FTS-only (`dense_degraded=True`) when the question embed
      raises `OllamaError`, instead of exiting 1.
- [ ] `uv run pytest -q` and `uv run mypy .` pass repo-wide; forecast within the
      800-line review budget.
