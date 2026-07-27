# Apply Progress: init-embedding-model-choice

## Scope covered so far

- PR1 (Phase 1: Foundation — `config.py` + template) — complete.
- PR2 (Phase 2: `llm/ollama.py` dimension error; Phase 3: `state/reindex.py`
  fatal handling) — complete (THIS batch).
- PR3 (Phase 4: `cli/main.py` wiring) — NOT started, out of scope for this run.
- Phase 5 (cross-PR verification/follow-up filing) — NOT started.

## Mode

Strict TDD (RED → GREEN → REFACTOR per task), both batches.

## Completed Tasks — PR1 (Phase 1)

- [x] 1.1 RED: `tests/unit/test_config.py::test_default_embedding_model_in_allowlist`
- [x] 1.2 GREEN: `EMBEDDING_MODEL_ALLOWLIST: tuple[str, ...] = (DEFAULT_EMBEDDING_MODEL,)` in `config.py`
- [x] 1.3 RED: `validate_embedding_model` parity tests (trim/colon, unsafe values, YAML indicators, reserved words, off-allowlist acceptance)
- [x] 1.4 GREEN: extracted `_validate_model_token(tag, field)`; `validate_model`/`validate_embedding_model` both delegate to it
- [x] 1.5 RED: `write_config` dual-placeholder substitution + independent placeholder-count guard tests
- [x] 1.6 GREEN: `write_config(root, model=DEFAULT_MODEL, embedding_model=DEFAULT_EMBEDDING_MODEL)` — validates and substitutes both placeholders independently
- [x] 1.7 GREEN: added `embedding_model: __OPENKOS_EMBEDDING_MODEL__  # 1024-dim; changing it forces a full re-embed` under `model:` in `openkos.yaml.template`
- [x] 1.8 GREEN: corrected `Config`'s docstring (no longer claims `embedding_model` is absent from the template); also corrected `DEFAULT_EMBEDDING_MODEL`'s own docstring, which made the same now-stale claim
- [x] 1.9 GREEN (collateral): updated `tests/unit/test_config.py`'s `_expected_config_bytes` helper to substitute both placeholders, and extended it with `embedding_model` param; added corresponding new tests. See Deviations — the actual byte-identity assertions live in `test_config.py`, not `test_init.py`.

## Completed Tasks — PR2 (Phase 2 + 3)

- [x] 2.1 RED: `tests/unit/llm/test_ollama.py::test_embed_row_wrong_length_raises_dimension_mismatch_not_generic_error` — a 768-length row raises `OllamaEmbeddingDimensionMismatch`, not generic `OllamaError`
- [x] 2.2 GREEN: added `OllamaEmbeddingDimensionMismatch(OllamaError)` in `llm/ollama.py`; message states both the actual row length and expected `EMBED_DIM`, and explicitly says "permanent dimension mismatch ... not a transient failure"
- [x] 2.3 GREEN: `_validate_embedding_row`'s wrong-length branch now raises `OllamaEmbeddingDimensionMismatch` directly (not `ValueError`) — bypasses `_embed_once`'s `except (JSONDecodeError, KeyError, TypeError, ValueError)` rewrap since it is not a `ValueError`
- [x] 2.4 RED: `test_embed_dimension_mismatch_never_retried` — dimension mismatch triggers zero `sleep` calls (not retried), `urlopen` called exactly once
- [x] 2.5 GREEN — safety-critical ordering: `embed()`'s retry loop now has `except (OllamaModelNotFound, OllamaEmbeddingDimensionMismatch): raise` BEFORE `except OllamaError:` (retry-with-backoff)
- [x] 2.6 RED: `test_embed_row_non_numeric_value_still_raises_generic_ollama_error` — non-numeric row of correct length still raises generic `OllamaError`, not the new subclass (D7 scope-discipline regression guard)
- [x] 2.7 RED: `test_embed_malformed_or_missing_key_response_stays_generic_ollama_error` (parametrized: malformed JSON, missing vector key) and `test_embed_singular_key_wrong_dimension_also_raises_dimension_mismatch` — parity unaffected by the new branch, and the branch applies identically to the legacy singular `embedding` key
- [x] 3.1 RED: `tests/unit/state/test_reindex.py::test_reindex_dimension_mismatch_mid_loop_propagates_and_stays_unrecorded` — `OllamaEmbeddingDimensionMismatch` propagates out of `reindex()`; `embed_failed` stays `0`; no `upsert_many`/`commit`/`write_model_tag` (asserted via empty `meta_hashes()` and `read_model_tag() is None`)
- [x] 3.2 GREEN — safety-critical ordering: `state/reindex.py`'s per-doc embed `try` now catches `(OllamaUnavailable, OllamaModelNotFound, OllamaEmbeddingDimensionMismatch)` and re-raises, placed BEFORE the broad `except OllamaError:` (embed_failed increment)
- [x] 3.3 RED: `test_reindex_dimension_mismatch_message_is_permanent_never_will_retry` — the propagated exception's message names it a permanent dimension mismatch and never says "will retry next run"
- [x] 3.4 GREEN: the message text is built once, at the source, in `OllamaEmbeddingDimensionMismatch`'s raise site in `llm/ollama.py` (task 2.2) — `reindex.py` never catches-and-rewraps it, so the same wording propagates unchanged to any caller (deviation note below)
- [x] 3.5 RED (regression, via pre-existing tests): `test_reindex_ollama_unavailable_mid_loop_is_reraised_not_counted_as_embed_failed` and `test_reindex_ollama_model_not_found_mid_loop_is_reraised_not_counted_as_embed_failed` re-ran green, unaffected by the added tuple member
- [x] Added `test_reindex_dimension_mismatch_ordering_precedes_generic_ollama_error_catch` — a dedicated ordering-proof test (not merely type-assertion) that would FAIL if the fatal tuple were removed/reordered: asserts `embedder.call_count == 2` (a.md succeeds, z.md raises, a third doc is never reached), which only holds if the dimension-mismatch branch stops the loop instead of falling into the generic `embed_failed`-and-continue branch.

## Files Changed — PR2

| File | Action | What Was Done |
|------|--------|---------------|
| `src/openkos/llm/ollama.py` | Modified | Added `OllamaEmbeddingDimensionMismatch(OllamaError)`; `_validate_embedding_row` raises it directly on wrong length (not `ValueError`); `embed()`'s retry loop exempts it from retry alongside `OllamaModelNotFound` |
| `src/openkos/state/reindex.py` | Modified | Added `OllamaEmbeddingDimensionMismatch` to the fatal re-raise tuple in the per-doc embed loop, before the generic `except OllamaError:`; updated module docstring |
| `tests/unit/llm/test_ollama.py` | Modified | Added Phase 13 (`OllamaEmbeddingDimensionMismatch`): 8 new tests covering raise-not-generic, message wording, subclass relationship, zero-retry, non-numeric-row regression guard, malformed/missing-key parity, singular-key parity |
| `tests/unit/state/test_reindex.py` | Modified | Added 3 new tests: fatal propagation + non-recording, safety-critical ordering proof, message wording guard |

## TDD Cycle Evidence — PR2

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 2.1/2.2/2.3 | `tests/unit/llm/test_ollama.py` | Unit | ✅ 119/119 baseline (`test_ollama.py` + `test_reindex.py` combined) | ✅ `ImportError: cannot import name 'OllamaEmbeddingDimensionMismatch'` (collection-time, whole file) | ✅ 87/87 passed after adding the exception class + raise site | ✅ wrong-length via both `embeddings` and singular `embedding` keys; message-content assertion (768 and 1024 both present) | ➖ None needed — pure exception class + one raise-site swap |
| 2.4/2.5 | `tests/unit/llm/test_ollama.py` | Unit | ✅ (same baseline) | ✅ failed pre-2.5 (fell into generic retry branch, would have called `sleep`) | ✅ `sleep_spy.calls == []`, `urlopen` called once | ➖ Single scenario (retry-loop exemption is binary) | ➖ None needed |
| 2.6/2.7 | `tests/unit/llm/test_ollama.py` | Unit | ✅ (same baseline) | ✅ written to lock in existing (unchanged) behavior as regression guards — these passed once the exception class existed, since only the length branch changed | ✅ passed | ✅ 2 malformed-response cases (bad JSON, missing key) + 1 singular-key parity case | ➖ None needed |
| 3.1/3.2 | `tests/unit/state/test_reindex.py` | Unit | ✅ 130/130 baseline (after PR2's `test_ollama.py` changes landed) | ✅ `Failed: DID NOT RAISE OllamaEmbeddingDimensionMismatch` (both propagation test and ordering-proof test) | ✅ 133/133 passed after adding the tuple member | ✅ dedicated ordering-proof test asserting `call_count == 2`, distinct from the plain propagation test | ➖ None needed |
| 3.3/3.4 | `tests/unit/state/test_reindex.py` | Unit | ✅ (same baseline) | ✅ `Failed: DID NOT RAISE` (same underlying gap as 3.1 — the doc was swallowed as `embed_failed` instead) | ✅ passed once 3.2's GREEN landed — message wording already satisfied by 2.2's message text, verified end-to-end through `reindex()`'s propagation path | ➖ Single scenario (wording is a fixed-text guard, not branching logic) | ➖ None needed |

### Test Summary — PR2
- **Total tests written**: 11 (8 in `test_ollama.py`, 3 in `test_reindex.py`)
- **Total tests passing**: 87/87 in `test_ollama.py`, 43/43 in `test_reindex.py` (130/130 combined, up from 119 baseline)
- **Layers used**: Unit (11)
- **Approval tests** (refactoring): None — additive only, no pre-existing behavior changed. All pre-existing `OllamaUnavailable`/`OllamaModelNotFound`/generic-`OllamaError` tests in both files re-ran green unmodified, serving as regression guards for the reordered `except` clauses.
- **Pure functions created**: 0 new (one exception class; one raise-site type swap; one `except` tuple extension)

## Work Unit Evidence — PR2

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/llm/test_ollama.py tests/unit/state/test_reindex.py -q` → `130 passed in 3.34s` |
| Runtime harness command/scenario and exact result | N/A — fake `_urlopen`/embedder fixtures per existing test patterns (per tasks.md's own forecast for Unit 2); no live Ollama needed |
| Rollback boundary | Revert `src/openkos/llm/ollama.py`, `src/openkos/state/reindex.py`, `tests/unit/llm/test_ollama.py`, and `tests/unit/state/test_reindex.py` only. Restores the prior (buggy) transient classification of a dimension mismatch. Purely additive type removal — no other file touched, independent of PR1's config/template changes (design rollback §2). |

## Local Gate (full repo) — after PR2

- `uv run pytest` → `2316 passed in 80.02s`
- `uv run pytest --cov` → `Required test coverage of 90.0% reached. Total coverage: 97.59%` (`2316 passed`)
- `uv run ruff check src tests` → `All checks passed!`
- `uv run ruff format --check src tests` → `140 files already formatted`
- `uv run mypy` → `Success: no issues found in 140 source files`

## Deviations from Design — PR2

1. **Tasks 3.3/3.4 scope clarification**: `state/reindex.py` has no stderr I/O of its own (it is a pure function that returns a `ReindexReport` or raises) — the actual `typer.echo(..., err=True)` call sites live in `cli/main.py`, which is explicitly out of scope for this run (Phase 4/PR3). I interpreted "the stderr message on this path" as the propagated exception's own message: it is built ONCE, at the source, inside `OllamaEmbeddingDimensionMismatch`'s raise site in `llm/ollama.py` (task 2.2), and `reindex.py` re-raises it completely unchanged (task 3.2's fatal-tuple addition, not a catch-and-rewrap). I wrote that message to already satisfy the "permanent, never 'will retry next run'" wording constraint, then added a dedicated `test_reindex_dimension_mismatch_message_is_permanent_never_will_retry` test that verifies this wording survives propagation through `reindex()` end-to-end. No separate message-building code was needed in `reindex.py` itself; 3.4's GREEN is satisfied by 2.2's message text plus 3.2's unmodified re-raise. This keeps the change strictly within the `llm/ollama.py` + `state/reindex.py` file-scope constraint (no `cli/main.py` edits), and leaves the actual user-facing stderr wording (naming the fix: restore `embedding_model`, re-run) as PR3's task 4.17 responsibility, per the design's own call-site audit table.
2. **Ordering-proof test added beyond the literal task list**: the prompt's "THREE SAFETY-CRITICAL ORDERING RULES" section required a test that actually FAILS if the clauses are reordered, not merely a type-assertion test. `test_reindex_dimension_mismatch_mid_loop_propagates_and_stays_unrecorded` alone would still pass even if the ordering were wrong in a subtly different way (a bare `except OllamaError` also catches the subclass and would still raise it via `raise` inside that broader clause IF that clause simply re-raised — but the actual generic branch does NOT re-raise, it increments `embed_failed` and continues). I added `test_reindex_dimension_mismatch_ordering_precedes_generic_ollama_error_catch`, which proves the ordering by asserting `embedder.call_count == 2` across three queued docs (proving the loop stopped immediately and never reached the third doc) — this assertion would fail if the fatal clause were removed or placed after the generic one. This test was not explicitly enumerated as its own task ID but directly fulfills the prompt's ordering-rule requirement for the `state/reindex.py` site (2.5's `llm/ollama.py` site is proven not-retried by `test_embed_dimension_mismatch_never_retried`'s `sleep_spy.calls == []` + single-`urlopen`-call assertion, which likewise would fail under a reversed `except` order since the generic branch DOES retry-with-sleep).

## Issues Found — PR2

None.

## Remaining Tasks (out of scope for this run — PR3)

- [ ] Phase 4: `cli/main.py` wiring (4.1–4.17)
- [ ] Phase 5: Verification (5.1–5.3)

## Workload / PR Boundary

- Mode: stacked-to-main (chain strategy from tasks.md)
- PR1 (merged/prior batch): Unit 1 — `config.py`/template
- PR2 (this batch): Unit 2 — `OllamaEmbeddingDimensionMismatch` + `state/reindex.py` fatal handling; branched from `main` at `444e513` on `feat/embedding-dimension-mismatch-fatal`, targets `main` directly per stacked-to-main
- Boundary: starts from `llm/ollama.py`/`state/reindex.py` with no distinct dimension-mismatch error type; ends with a permanent, non-retried `OllamaEmbeddingDimensionMismatch` correctly ordered ahead of the generic `OllamaError` catch at both the client-retry and reindex-fatal-handling call sites — independently revertible per design rollback §2
- Estimated review budget impact: PR2 diff is small (~2 production files, ~11 new tests) — well under the 800-line session budget and the 400-line default

## Status

21/21 Phase 1–3 tasks complete (9 PR1 + 12 PR2). Ready for verify (of PR2's slice) / ready for `sdd-apply` to continue with PR3 (Phase 4, `cli/main.py` wiring) in a subsequent run.
