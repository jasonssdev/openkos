# Status: COMPLETE — archived with one open verification follow-up (2026-08-26)

## Summary

Retrieval chunking shipped: `state/reindex.py` now splits a document's embed
text into a repeated header plus body chunks, `state/vectorstore.py` derives
one document-level vector per document from those chunks (normalized mean of
normalized chunk vectors), and `k-NN` query collapses chunk hits back to one
result per document. `retrieval/answer.py`, `retrieval/fusion.py`, and
`graph/proximity.py` needed ZERO production changes — the collapse at the
vector-store boundary made chunking invisible to every downstream consumer.

Full test suite: 5687 passed, 1 skipped. `mypy .` clean (273 files). `ruff
check .` / `ruff format --check .` clean. No CRITICAL findings at any point in
this cycle.

## What's done

- Body-only chunking with a repeated header (`embedding-chunking` capability,
  new).
- Chunk-aware vector schema, migration of legacy stores, atomic orphan-free
  multi-chunk upsert, document-vector derivation, deterministic k-NN
  tie-breaking (`vector-store`).
- Per-chunk embed calls with all-or-nothing per-document failure isolation;
  the three FATAL Ollama subclasses (`OllamaUnavailable`,
  `OllamaModelNotFound`, `OllamaEmbeddingDimensionMismatch`) still abort the
  run immediately mid-chunk-loop, never silently downgraded to a per-document
  skip (`reindex-command`).
- Corrected re-embed-trigger disclosure that distinguishes a genuine model
  change from a composition-only bump like this one (`reindex-command`).
- `purge`'s deferred-reembed warning updated to pre-empt the corrected
  wording (`privacy-purge`).
- Dense retrieval reaches a document's tail past the old truncation
  boundary; chunk collapse stays invisible to citations, attribution, and
  save provenance; the sensitivity fail-closed re-check still runs before
  any chunk's content reaches the LLM, now covered by a dedicated runtime
  test added in the branch's final commit (`query-answer`).
- Embedding-proximity graph edges are now derived from full chunk-backed
  document vectors instead of a single truncated prefix vector
  (`graph-projection`).

## Open follow-up (task 5.2, deliberately not run this batch)

`evals/edge_typing/`, `evals/contradictions/`, and `evals/query_identity/`
were not re-run against their recorded bands. Rationale, independently
checked by `sdd-verify`: `edge_typing` and `contradictions` score classifiers
over fixed fixtures that do not depend on live proximity nomination, and
`query_identity` measures the question-vector space this change explicitly
never touches. Production diff in the paths those evals exercise
(`fusion.py`, `resolution/*`) is zero. Risk assessed low but genuinely
unmeasured — left for a follow-up verification pass. This is recorded as an
explicit archive exception, not a completed task; see `archive-report.md`
for the full Task Completion Gate reasoning.

## Known, disclosed, non-blocking measurement caveat

The D9 pair-nomination probe's margin is negative both before (`-0.0328`) and
after (`-0.0298`) this change, on the corrected 10-pair fixture (commit
`5b596c2`). The gate's only claim is that chunking did not widen that
pre-existing gap — it does not claim positive/negative separation exists.

## Delivery

Branch `retrieval-chunking-888`, local only, not pushed, no PR opened. This
archive did not commit, push, or open a PR — the maintainer decides delivery.

See `archive-report.md` for the full Final-State Authority reconciliation,
spec-sync detail, and mechanical-copy verification evidence.
