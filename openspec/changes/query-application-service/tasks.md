# Tasks: Query Application Service

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~2,365 total (Slice 1 ~1,170 / Slice 2 ~1,195), per design's Slice Boundaries table |
| Effective review budget (session override) | 2,000 changed lines per PR (`review_budget_lines`) — both slices fit |
| 400-line budget risk | High (each slice is ~3x the skill's nominal 400-line guard; both fit under the session's 2,000-line override) |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (read path + 123-site `answer` patch-target migration) → PR 2 (`--save` filing domain logic) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Read-path extraction into `application/query.py` + migrate all 123 `answer` patch targets across 5 test files | PR 1 | `uv run pytest tests/unit/cli/test_query.py tests/unit/application -q` | `uv run openkos query "<question>"` against a seeded workspace | Revert `src/openkos/application/`, the `main.py` read-path diff, and the patch-target renames together (GREEN depends on the move) |
| 2 | `--save` filing domain logic extraction | PR 2 | `uv run pytest tests/unit/cli/test_query_save.py tests/unit/application -q` | `uv run openkos query "<question>" --save --auto` against a seeded workspace | Revert the filing/title/duplicate-scan additions to `application/query.py` and the `main.py` `--save` shim; PR 1 stays fully functional standalone |

## Slice 1 — PR 1 (~1,170 lines)

### Phase 0: Boundary Verification (blocking — before any file changes)
- [ ] 0.1 Read `src/openkos/cli/main.py:16958-17601`. Confirm no statement after the `with (vector_store_cm, fts_index_cm)` block closes (~line 17050) references `vector_store`/`fts_index` directly — only via `result`'s fields. This validates the design's read-path/render-path split. If refuted, STOP and re-derive the slice boundary before Phase 1.

### Phase 1: Foundation
- [ ] 1.1 Create `src/openkos/application/__init__.py`: docstring only, no re-exports (mirrors `retrieval/__init__.py`, satisfies D5).
- [ ] 1.2 RED: `tests/unit/application/test_layering.py::test_query_module_never_imports_cli` — AST-scan `application/query.py`'s imports and assert none reference `openkos.cli`. Must fail (module absent).
- [ ] 1.3 GREEN: create `src/openkos/application/query.py` with module docstring and the `QueryOutcome` dataclass; no `openkos.cli` import. Layering test passes.

### Phase 2: Store composition + `answer()` call
- [ ] 2.1 RED: `test_query_service.py::test_run_query_degrades_on_missing_vector_store` / `..._missing_fts` — assert `QueryOutcome.vector_store_unavailable`/`fts_unavailable` flip True, no exception.
- [ ] 2.2 GREEN: move `_open_vector_store_or_degrade`, `_open_fts_or_degrade` (private, `main.py:16260-16354`) into `application/query.py`; implement `run_query(...)` per the design's signature.
- [ ] 2.3 RED: `test_run_query_propagates_{ollama_unavailable,model_not_found,embedding_dimension_mismatch,generic_ollama_or_fts_error}` — assert `run_query` raises each type unwrapped (D2), no re-wrapping.
- [ ] 2.4 GREEN: import `answer` from `retrieval.answer`, call it unqualified inside `run_query`; remove the try/except from `main.py`.
- [ ] 2.5 REFACTOR: add `run_query`'s `Raises:` docstring stating the three specific `OllamaError` subclasses MUST be handled before the generic `(FtsUnavailable, OllamaError)` catch-all.

### Phase 3: Patch-target migration (rides with this slice per D1 — not its own slice)
- [ ] 3.1 Migrate `monkeypatch.setattr("openkos.cli.main.answer", ...)` → `"openkos.application.query.answer"` in `tests/unit/cli/test_query.py` (57) and `test_query_save.py` (59).
- [ ] 3.2 Migrate the remaining sites: `test_write_time_refresh.py` (4), `test_embed_host_advisory.py` (2), `test_adjudicate.py` (1).
- [ ] 3.3 `grep -rn 'cli.main.answer' tests/` returns zero matches; run the five affected test files.

### Phase 4: Adapter wiring
- [ ] 4.1 Replace `main.py`'s inline store-open/try-except/`answer()` block (`16954-17048`) with a call to `application.query.run_query(...)`; re-add the four `except` handlers around the call site, unchanged in order and text.
- [ ] 4.2 Keep `_stale_index_names` and its warning (`main.py:16971-16977`) in the CLI, called BEFORE `run_query` — this preserves stderr ordering byte-identically (D1).
- [ ] 4.3 Forbid `answer_fn: AnswerFn = answer` or any default-argument seam in `application/query.py`/`main.py` (a default binds at `def` time and silently defeats `monkeypatch.setattr`). Verify: `grep -n 'AnswerFn = answer\|: .*= answer$' src/openkos/application/query.py src/openkos/cli/main.py` returns no matches.
- [ ] 4.4 Update non-`--save` `typer.echo` rendering in `main.py` to read `QueryOutcome` fields instead of local variables.

### Phase 5: Slice 1 gate
- [ ] 5.1 `uv run pytest tests/unit/cli/test_query.py -q` (58 tests); byte-identical stdout/stderr/exit codes for a stale-index case, a store-degrade case, and each `OllamaError` subclass.
- [ ] 5.2 `uv run pytest --cov=src/openkos/application tests/unit/application -q`; 90% branch coverage on degrade/exception branches.
- [ ] 5.3 `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy .` (whole repo — CI lints all of it).
- [ ] 5.4 `uv run openkos query "<question>"` against a seeded workspace; output matches the pre-change baseline.

## Slice 2 — PR 2 (~1,195 lines)

### Phase 6: Filing domain logic
- [ ] 6.1 RED: `test_stage_filed_answer_refuses_empty_citations` — `ValueError` on zero citations.
- [ ] 6.2 GREEN: move `_FiledAnswerPlan`→`FiledAnswerPlan`, `_stage_filed_answer`→`stage_filed_answer` (`16584-16740`), and the title cascade (`_declarative_answer_title`, `_question_subject`, `_clause_answer_title`, both `_DECLARATIVE_TITLE_*` constants, `16355-16482, 16515-16583`) into `application/query.py`; only `stage_filed_answer`/`FiledAnswerPlan` are public.
- [ ] 6.3 RED: `test_grounding_unverified_true_when_unattributed`, `test_synthesis_share_warrants_warning_at_threshold`.
- [ ] 6.4 GREEN: add `grounding_unverified(result) -> bool` (D4) and `synthesis_share_warrants_warning(citations) -> bool` (replaces the inline `_SYNTHESIS_SHARE_WARN_THRESHOLD` comparison); both pure.
- [ ] 6.5 RED: `test_scan_for_duplicates_reports_when_index_absent` (degrade) and a positive-match case.
- [ ] 6.6 GREEN: move the duplicate-scan orchestration (`17407-17426`) into `scan_for_duplicates(question, *, layout, cfg, embedder)`.
- [ ] 6.7 REFACTOR: confirm `_no_match_message` stays in `main.py` (pure presentation, D3) — do not move it.

### Phase 7: Adapter wiring — `--save` shim
- [ ] 7.1 Replace `main.py`'s inline staging block with calls to `stage_filed_answer`, `grounding_unverified`, `synthesis_share_warrants_warning`, `scan_for_duplicates`; keep both confirm gates (`17454-17468`, `17469-17478`) verbatim in the CLI.
- [ ] 7.2 Keep `_snapshot_read`, `insert_index_entry`, `insert_log_entry`, `_reject_drifted_targets`, `fsio.write_*`, `_autocommit`, `_refresh_derived_after_write`, `_chat_client`, `_resolve_local_exemption`, `_warn_if_nonlocal_embed_host`, and every `observability.*` call in `main.py` unmodified and un-duplicated.
- [ ] 7.3 Add a one-line test-local alias, `tests/unit/cli/test_query_save.py:20`: `from openkos.application.query import stage_filed_answer as _stage_filed_answer` — the 22 existing call sites stay untouched.
- [ ] 7.4 Repoint the 3 stale prose-only references: `tests/unit/state/test_derived.py:404`, `tests/unit/bundle/test_cited_high_water_raises.py:7`, `tests/unit/cli/test_slugify.py:203`.

### Phase 8: Slice 2 gate
- [ ] 8.1 `uv run pytest tests/unit/cli/test_query_save.py -q` (103 tests); byte-identical stdout/stderr/exit codes for a successful save, zero-citation refusal, an unattributed-citation confirm decline, a duplicate-question disclosure, and the nonlocal-embed-host advisory. Diff stderr line order against the pre-change baseline explicitly — the two decisions protecting ordering (staleness stays in the adapter; `observability.*` never enters the service) are load-bearing here.
- [ ] 8.2 `uv run pytest --cov=src/openkos/application tests/unit/application -q`; 90% branch coverage on filing/title/duplicate-scan branches.
- [ ] 8.3 `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy .` (whole repo).
- [ ] 8.4 `uv run openkos query "<question>" --save --auto` against a seeded workspace; output and the written bundle entry match the pre-change baseline.

### Phase 9: Documentation
- [ ] 9.1 Confirm `docs/adr/0018-application-layer-for-bounded-context-services.md` status stays `Proposed` (flips to Accepted only at archive).
- [ ] 9.2 Leave `specs/query-command/spec.md`'s Purpose delta for archive-time merge — no action during apply.
- [ ] 9.3 Note in both PR descriptions: `AGENTS.md`'s Conventional Commit scope list has no `application` entry; both slices use scope `cli` (touched adapter is `main.py`); adding a scope is a separate documentation change (open question, non-blocking).
