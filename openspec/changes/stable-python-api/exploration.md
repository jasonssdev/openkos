# Exploration: stable-python-api (MVP 6 stage, ADR-0039) + issue #1391

## Current state (evidence)

- **No public API surface exists.** `src/openkos/__init__` has no `__all__`/`__version__`; the version is read only via `importlib.metadata` (`cli/main.py:21`, `mcp/server.py:33`, `model/okf.py:26`); `pyproject.toml` version is 0.5.5. `application/__init__.py:1-16` exports nothing ("callers import the context module directly"). `__all__` exists only in `runner.py:735`, `watch.py:876`, `digest.py:112`, `resolution/__init__.py:41`.
- **Service shape is already consumer-friendly:** frozen dataclasses, ports dataclasses with real defaults (`judging.py:293` `JudgingPorts`; only `chat_client` required), observers, `StopToken` (`runtime.py:44`), `CommitSection` port (`lock_wait.py:24`, `locked_commit_section` `:75`; busy is `lock.WorkspaceBusyError`, retried by `acquire_with_backoff` `:35`). Typed refusals exist but share no root: `write_gate.WriteRefused` family (`write_gate.py:40-90`), `IngestRefused` family (`ingest_service.py:70-134`), `JudgingRefused` (`judging.py:107`), `BudgetRefused`, `ListUsageError`, `ConceptNotFound`.
- **Inconsistent workspace handle:** some services take `root: Path` (`judging.run_judging`, `ingest_source`, `suggest_*`), others `config.WorkspaceLayout` (`curate_decisions.list_open_decisions` `:1183`, `status`, `pending_queue_report.read_report` `:268`, `concept_read`). `run_query` needs layout + cfg + llm + embedder pre-built (`query.py:111`).
- **The composition root lives in the CLI:** `cli/main.py` (16k lines) holds `_chat_client` `:266`, `_embed_client` `:286`, `_resolve_local_exemption` `:4056`, `_open_proximity_or_degrade` `:12576`, `_autocommit` `:1458`, `_refresh_derived_after_write` `:4309`. `application/backends.py` already holds the resolvers and `BackendFactories` (`:109`), but the patch-friendly factory wiring is in the CLI. `application/` may not import the CLI (`tests/unit/application/test_layering.py`), so a Python API needs its own default wiring.
- **Owner-local surfaces:** `curate_decisions.py` and `judging.py` are documented as internal / owner-local; no disclosing adapter may import them. The layering test only checks for `isatty` (`test_layering.py:133`); the "no mcp/api import" guard promised in `curate_decisions.py:1-18` must be verified. The sensitivity gate lives only in `mcp/gate.py` (`take_snapshot`, `disclose_*`); ADR-0058 says the local API applies it to every response, with decision payloads owner-local.
- **Cancellation:** only a cooperative `StopToken` between chunks (judging, runner); nothing preempts a model call (ADR-0021). Ingest and query have none. **Progress:** per-service observers/callbacks (`JudgingObserver`, `IngestObserver`, query `ProgressCallback`). **Backend errors:** neutral `Backend*` in `llm/base.py`; the `run_query` docstring still names `OllamaError` (`query.py:132-148`).
- **Leaky types:** `run_query` returns the internal `retrieval.answer.AnswerResult`; `DecisionItem.payload` is an unversioned stored-JSON `Mapping[str, object]`; `curate_queue.OpenRow` wraps the internal `state.pending_queue.PendingItem` (not part of `DecisionItem`).

## Inventory against the desktop minimum scope

- **Needed (public-surface candidates):** `ingest_service`, `query`, `pending_queue_report`, `curate_decisions`, `judging`, `status`, `doctor`, `backends` (endpoint and install hints for guided setup), `concept_read`, `list_service`, `lock_wait`, `runtime.StopToken`, and a new refresh service (#1391).
- **Inbox watch:** runs as a daemon job (`run_watch_job`); the app may drive it through the sidecar without it being public.
- **CLI-only or internal:** `budget`, `consent`, `digest`, `drift`, `consistency`, `commit_phase`, `catalog_delta`, `repair`, `next_action`, `watch_notify`, `query_report`, `revisions*`, `lint`, `lifecycle` / `merge_service` / `unmerge_service` / `reconcile_service` (reached through `curate_decisions`), `export_service` / `import_service`, `contradictions_service`, `duplicates_service`, `auto_merge`, `reindex_service`, `suggest_*_service` (reached through `judging`), `write_gate` (exceptions only).

## Issue #1391: how the daemon does it today

- **Identity** (`cli/daemon.py:254-290`) is already LLM-free: `duplicates_service.report_duplicates` → drop `judged_different_groups` → `producers.enqueue_identity(..., adjudicated=identity_adjudication.servable_stored_verdicts(...))`. Judging's write-through is amend-only (`curate_queue.set_identity_verdicts`, `curate_queue.py:370`), so a judged group appears only after a producer pass inserts its row. `enqueue_identity` already passes `amend_same_inputs=True` (`queue_producers.py:344`), so `pending --stats` should not inflate; this needs a test.
- **Relations and volatility** (`daemon.py:293-361`) call `suggest_relations` / `suggest_volatility_tiers` with `max_calls=budget.remaining`, then `enqueue_relations` / `enqueue_volatility`. Both enqueue functions take the run's outcome, not stored state; no model-free entry point exists. Judging persists verdicts in the edge-suggestions store and the volatility cached answers, read back via `partition_edge_suggestion_serves` (`judging.py:687-701`) and `cached_answers`.
- **Lock:** the commit section is entered only inside `pq.upsert_proposal`, via `publish` (`queue_producers.py:104`). The app would pass `lock_wait.locked_commit_section`.

| Option for relations and volatility | Pros | Cons |
|---|---|---|
| A. Call the existing services with `max_calls=0` and a no-model client | Reuses the daemon path | Builds a client and resolves the backend; relations walks the graph and proximity store; `deferred_by_bound > 0` makes `complete=False`, so nothing is retired |
| B. Store-only builder that makes the outcome from served rows | No client or key resolution; `complete` computed honestly | New code for both kinds |

Recommendation: B, plus lifting the identity stage from `daemon.py` into `application/`, as the judging change did for identity adjudication.

## Public package options

| Approach | Pros | Cons | Effort |
|---|---|---|---|
| 1. New `openkos.api` facade with explicit `__all__`; API-owned dataclasses only where internals leak | No module moves; slices stay small | Re-exported dataclasses freeze their field sets; needs its own default port wiring | Medium |
| 2. Declare selected `application/` modules public in place | Almost no code | Public and owner-local helpers stay mixed; nothing enforces which is which | Low |
| 3. Move modules into a public package | Clean physical boundary | Heavy churn across `cli/main.py`, the daemon and `mcp/`; contradicts "start lean" | High |

**Enforcement:** a golden snapshot test of public signatures, dataclass fields and the exception hierarchy; an `__all__` completeness test; a guard that `openkos.api` imports only `application/` and leaf types, never `mcp.gate` or `cli`; a check that no public signature mentions `sqlite3`, `typer` or `state.*` types.

**Versioning:** the package is 0.x, so its semver promises nothing. Recommended: a separate integer `API_VERSION`, additive within a major; any breaking snapshot diff forces a bump.

**Recommendation:** approach 1, a small first slice, snapshot-test enforcement, and a new ADR (Proposed) covering what is public and the compatibility policy.

## ADR and docs

- A new ADR is needed: it fixes a public interface and a compatibility promise, both hard to reverse.
- Docs to update: `docs/architecture.md` (internal seams and extension points, the future local API edge, extension points built on the stable API, the package tree if `openkos/api` is added); `docs/roadmap.md` MVP 6 stage; the `application/__init__.py` docstring; possibly the `AGENTS.md` "Start lean" and layering paragraphs.

## Candidate minimal first slice

`openkos.api` with a `Workspace` handle; the read side (status, pending report, `list_open_decisions`); decision preview and apply; judging quote and run; `refresh_decision_queue` (#1391); the snapshot test; the ADR. Ingest, query, doctor and setup deferred to later slices (they need port wiring and error mapping).

## Open decisions

1. v1 scope: read, decisions, judging and refresh only, or also ingest, query, inbox and doctor/setup.
2. Package shape: `openkos.api` facade (recommended), in-place declaration, or a move.
3. Versioning: separate `API_VERSION` integer (recommended) or package semver; what pre-1.0 guarantees.
4. Workspace handle: a `Workspace` object or functions taking a `Path`.
5. Error taxonomy: a new `OpenKosError` hierarchy mapping existing exceptions, or re-export the existing ones.
6. Wiring: the API owns default backend wiring (extracting the composition root from `cli/main.py`), or the caller passes ports.
7. #1391: option A or B; whether refresh also retires stale rows or only inserts and amends; result shape (per-kind `ProducerResult`).
8. Disclosure boundary: enforced in the local API only (recommended) or also in the Python API.
9. Delivery slicing under auto-chain; ADR timing.

## Risks

- Re-exported internal dataclasses (`DecisionItem`, `JudgingOutcome`, `AnswerResult`) freeze internal fields; the decision payload JSON is unversioned.
- Moving default wiring out of `cli/main.py` could break tests that patch `openkos.cli.main.*` (the autouse network guard in `tests/unit/conftest.py`).
- Cancellation is cooperative only; worst-case stop latency is about 2400 s for a relation chunk at the default `chat_timeout`.
- `PERSIST_ATTEMPTS` and backoff in `judging.py` are unmeasured; the judging archive also lists the suggest-volatility digest-guard gap and the missing rubric check in curate's identity serving.
- Nothing yet guards the API against disclosing owner-local payloads from `curate_decisions` and `judging`; that guard needs adding.
