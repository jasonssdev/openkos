# Design: Reindex Embedding Resilience

## Observable failure-path behavior (spec input)

**Decision: PARTIAL-PROGRESS commit. Survivors are committed and VISIBLE to `query`.**

When some docs fail to embed **with the transient generic `OllamaError`**
(the HTTP-400 EOF class) in a `reindex` run:

- Each doc that embeds successfully is upserted; each doc whose embed fails
  transiently (after retries are exhausted) counts as a **new dedicated
  `embed_failed`** tally — never embedded, never pruned. The run does **not**
  crash: exit 0.
- The run still commits **once at end** (unchanged atomic single-commit
  contract), covering every survivor + prune together. Survivors become
  queryable immediately. Reverting-to-nothing is rejected: it would discard
  good work and still not persist the tag, gaining nothing.
- **A fully-unreachable server (`OllamaUnavailable`) or a missing model
  (`OllamaModelNotFound`) is NOT a per-doc skip — it is FATAL** (re-raised,
  exit 1, existing "Error Ladder Mirrors query" scenario unchanged). An
  environment that cannot serve any embed is not a transient per-doc failure.
- **Two distinct skip kinds, kept separate in the report:**
  `skipped` remains the existing *permanent* class (file unreadable / parse /
  decode failure — re-run will NOT help). `embed_failed` is the new
  *transient* class (doc read fine, embed EOF'd — re-run WILL help). They are
  never conflated.
- **Model-tag self-heal gate keys on the UNION**: the `reindex.py:254` gate
  becomes `skipped == 0 and embed_failed == 0` — any doc left un-(re)embedded,
  by either cause, withholds the new `model_tag`. Total-skip behavior and the
  self-heal loop are otherwise unchanged: the store keeps the old/absent tag,
  the NEXT run re-forces the full re-embed (`model_changed` stays `True`),
  giving failed docs another chance until one run reaches
  `skipped == 0 and embed_failed == 0`, which finally persists the tag. No doc
  is ever permanently stranded on a stale old-model vector.
- **Actionable re-run notice keys ONLY on `embed_failed > 0`** — an stderr
  notice fires for transient embed failures (re-run recommended) but NOT for
  permanently-unreadable files. The model-switch-with-partial-failure
  transient (a mixed-model store until self-heal) surfaces through this same
  `embed_failed`-keyed notice.
- Report shape: `embedded = <survivors>`, `skipped = <permanent skips>`,
  `embed_failed = <transient embed failures>`, `model_reembedded` as today.

## Technical Approach

Three coordinated moves (measurement #1523: no single move suffices):
resilient reindex embedding, a reliable default model (`bge-m3`, ADR-0006),
and query-side degrade. The retry concern is split from the isolation concern.

## Architecture Decisions

| # | Decision | Choice | Rejected | Rationale |
|---|----------|--------|----------|-----------|
| D1 | Retry home | `OllamaClient.embed` (transparent, reusable) | in `reindex` only | Transport resilience is generic; the query-side question embed benefits too. Isolation stays run-aware in `reindex` (D2). |
| D2 | Isolation grain + **precise per-doc catch** | Per-doc embed loop in `reindex`: iterate `to_embed`, `embedder.embed([text])` each. **`except (OllamaUnavailable, OllamaModelNotFound): raise` FIRST (fatal, exit 1), THEN `except OllamaError: embed_failed += 1; continue`** (order matters — subclasses re-raised before the generic base is caught) | adaptive bisect sub-batch; bare `except OllamaError` | #1523: batch total is not the driver; failure is ~per-input. Per-doc is the exact grain. A bare `except OllamaError` would swallow a fully-unreachable server (`OllamaUnavailable`) or a missing model (`OllamaModelNotFound`) as "every doc skipped, exit 0", contradicting the existing "Error Ladder Mirrors query" mainline (unreachable/missing → exit 1). Only the generic transient `OllamaError` (400 EOF) is a per-doc `embed_failed`. |
| D3 | Retry trigger + policy | Client retries the **generic transient `OllamaError`** (400 EOF) with N attempts (default 3) + exponential backoff, both injectable (no real sleep in tests). **`OllamaModelNotFound` is NEVER retried** (a pull can't happen mid-run). `OllamaUnavailable` may retry but is ultimately FATAL if exhausted. Only after the generic `OllamaError` exhausts retries does `reindex` (D2) turn it into a per-doc `embed_failed` skip | retry all `OllamaError`; broad `Exception` | The EOF is HTTP 400 → `_map_http_error` (400≠404) → generic **`OllamaError`** (NOT `Unavailable`, NOT `ModelNotFound`). Retrying the two fatal subclasses is pointless/harmful. Retry reduces transient loss only; `bge-m3` is the reliability guarantee (ADR-0006). |
| D4 | Query degrade + **precise catch** (review correction) | `_dense_search`: `except (OllamaUnavailable, OllamaModelNotFound): raise` FIRST (fatal, propagates to `query`'s exit-1 ladder), THEN `except (VecUnavailable, sqlite3.Error, OllamaError): return [], True` (`dense_degraded`) | broad `Exception` (like `_graph_search`); unqualified `OllamaError` catch | Precise, mirroring D2's reindex-side split: a down server or a missing model is an environment failure, not a per-question transient — it must reach `query`'s existing fatal exit-1 ladder (`cli/main.py:2373`/`2380`), not silently degrade to FTS-only. The `bge-m3` default (D5) makes an unpulled model a common first-run trigger, raising the cost of masking it. Only the generic transient `OllamaError` (400 EOF) degrades. Accepts a retrieval→`llm.ollama` import, consistent with the existing `VecUnavailable` import; dense is additive like graph. |
| D5 | Default model | `DEFAULT_EMBEDDING_MODEL = "bge-m3"` (`config.py:23`) + docstring | keep qwen3 | ADR-0006: reliability-first. 1024-dim satisfies `EMBED_DIM`; migrates via tag gate. |

## Data Flow

    reindex: for (cid,text,digest) in to_embed:
                 try:  embedder.embed([text])  ──retry(OllamaClient)──▶ vector
                       items.append(...)
                 except (OllamaUnavailable, OllamaModelNotFound): raise   # FATAL exit 1
                 except OllamaError: embed_failed += 1 ; continue          # transient, not pruned
             upsert_many(items) ; prune
             tag iff (skipped==0 and embed_failed==0) ; commit()  (one)
             if embed_failed > 0: stderr re-run notice

    query: _dense_search → embedder.embed([q])
             except (OllamaUnavailable, OllamaModelNotFound): raise         # FATAL, query's exit-1 ladder
             except (VecUnavailable, sqlite3.Error, OllamaError) → ([], True)

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `llm/ollama.py` (~120) | Modify | Retry-with-backoff wrapper in `embed`; injectable attempts/backoff; skip `OllamaModelNotFound` |
| `state/reindex.py` (~222) | Modify | Replace single-batch embed with per-doc loop; precise catch (fatal `Unavailable`/`ModelNotFound` re-raise, transient generic `OllamaError` → new `embed_failed` tally); tag gate widens to `skipped==0 and embed_failed==0`; `embed_failed>0` stderr re-run notice; keep single end-of-run commit |
| `state/reindex.py` `ReindexReport` (~58) | Modify | Add `embed_failed: int` field (transient embed-EOF skips), **separate** from the existing `skipped` (permanent unreadable/parse/decode). Docstring both, and the union-keyed tag gate |
| `retrieval/answer.py` (~236) | Modify | Import `OllamaUnavailable`, `OllamaModelNotFound`, `OllamaError`; `_dense_search` re-raises the two fatal subclasses first, then degrades (`dense_degraded=True`) on the generic transient `OllamaError` alongside the existing `VecUnavailable`/`sqlite3.Error` catch |
| `config.py` (:23) | Modify | Default → `bge-m3` + docstring |
| `docs/adr/0006-*.md` | Create | ADR (done this phase) |
| `docs/tech_stack.md` | Modify | bge-m3 default, reliability-first framing (done this phase) |
| Tests | Modify | See below |

## Testing Strategy

| Layer | What | Approach |
|-------|------|----------|
| Unit (ollama) | `embed` retries transient `OllamaError` then succeeds; gives up after N → raises; `OllamaModelNotFound` not retried; no real sleep | fake `urlopen`, injected backoff |
| Unit (reindex) | transient-EOF doc → survivors committed, `embed_failed==1`, `skipped==0`, exit 0, re-run notice on stderr; `OllamaUnavailable`/`OllamaModelNotFound` mid-loop → re-raised, exit 1, no commit of survivors-only-then-crash side effects (error ladder unchanged); tag withheld when `embed_failed>0` OR `skipped>0`; next run re-forces; all-clear run persists tag; permanent skip does NOT fire re-run notice; single commit preserved | fake Embedder raising a chosen exception per marked text |
| Unit (answer) | question embed raising the generic transient `OllamaError` → `dense_degraded=True`, FTS-only, no raise; question embed raising `OllamaUnavailable`/`OllamaModelNotFound` → re-raised, propagates unswallowed | spy embedder |
| Integration | reindex realistic bundle completes without abort | real sqlite-vec |

## Test Churn (call out for sdd-tasks)

- **Per-doc grain breaks `embedder.call_count == 1`** at
  `tests/unit/state/test_reindex.py` lines **149/169/565/695** — update to
  per-doc count (one call per queued doc; 0 when none). Deliberate.
- **Default-change asserts**: `tests/unit/test_config.py:513`,
  `tests/unit/cli/test_reindex_cmd.py:152` (and any doctor tests) pin the old
  default → update to `bge-m3`.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary. In-process embed loop +
HTTP retry to a trusted local host + a config constant.

## Migration / Rollout

`bge-m3` requires `ollama pull bge-m3` (surfaced by `doctor`). Existing stores
carry the old model tag → one forced full re-embed via the tag gate, then
incremental. Self-healing; no destructive schema migration (1024-dim
unchanged). Rollback = revert edits; a bge-m3 store stays valid.

## Chained-PR Forecast

Single PR; ~4 small source edits + test updates. Est. under 400 lines.
- Decision needed before apply: No
- Chained PRs recommended: No
- 400-line budget risk: Low

## Interfaces / Contracts

```python
@dataclass(frozen=True)
class ReindexReport:
    embedded: int
    cache_hits: int
    pruned: int
    skipped: int          # PERMANENT: unreadable / parse / decode (re-run won't help)
    embed_failed: int = 0 # NEW, TRANSIENT: embed EOF after retries (re-run helps)
    prune_skipped: bool = False
    model_reembedded: bool = False
# Tag-persist gate: skipped == 0 AND embed_failed == 0
# stderr re-run notice: iff embed_failed > 0
```

## Open Questions

- Resolved this revision (was an open question): the per-doc catch must be
  precise — `OllamaUnavailable`/`OllamaModelNotFound` are FATAL (exit 1,
  existing error ladder), only the generic transient `OllamaError` is a per-doc
  `embed_failed` skip. See D2/D3.
- Retry attempt count (default 3) and backoff base are injectable defaults;
  final numbers can be tuned in `sdd-tasks`/apply without changing the contract.
