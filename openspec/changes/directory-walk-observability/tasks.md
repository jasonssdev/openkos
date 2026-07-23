# Tasks: Directory-Walk Observability Hardening (S3 follow-up)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~480-530 (helper ~50, 5 signal wirings ~12, 4 leak guards ~50, tests ~370) |
| 400-line budget risk | Medium |
| Chained PRs recommended | No |
| Suggested split | Single PR (fits 800-line session budget) |
| Delivery strategy | auto-chain |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Medium

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Ship `observability.py` helper + 5-verb STDERR signal wiring | PR 1 (single) | `uv run pytest tests/unit/cli/test_observability.py tests/unit/cli/test_query.py tests/unit/cli/test_contradictions.py tests/unit/cli/test_adjudicate.py tests/unit/cli/test_suggest_relations.py tests/unit/cli/test_suggest_volatility.py` | Real bundle with a `chmod 000` subdirectory + each verb CLI invocation (mirrors `test_okf.py:405-434`) | Revert `cli/observability.py` + 5 call sites in `cli/main.py`; no other file touched |
| 2 | Leak-closure re-check in 4 load paths | PR 1 (single) | `uv run pytest tests/unit/resolution/test_contradiction.py tests/unit/resolution/test_adjudication.py tests/unit/resolution/test_edge_typing.py tests/unit/resolution/test_volatility_typing.py` | N/A — no CLI-observable harness distinct from unit tests; leak-closure only observable via captured `llm.chat` payload, already exercised in the focused test command | Revert the 4 per-file guards independently; `sensitivity.py` and `answer.py` stay untouched, so revert never touches the pure leaf |

Both units ship in the same PR (single cohesive fail-closed invariant per design Decision 6); table documents independent rollback boundaries in case a partial revert is ever needed.

## Phase 1: Foundation — Signal Helper

- [x] 1.1 RED: `tests/unit/cli/test_observability.py` (new) — `warn_if_walk_incomplete` prints self-explaining STDERR message when `okf._walk_errors` is non-empty (monkeypatch `os.walk` onerror, `test_okf.py:405-434` pattern). Covers spec "Incomplete walk warns and still exits 0".
- [x] 1.2 RED: same file — clean bundle (no walk error) produces no STDERR output. Covers spec "Clean bundle produces no warning".
- [x] 1.3 RED: same file — `include_confidential=True` suppresses the warning even when `_walk_errors` is non-empty. Covers spec "`--include-confidential` suppresses the warning".
- [x] 1.4 RED: same file — `mode="refuse"` raises `NotImplementedError` (dead branch this slice, stable signature for future cloud-egress mode).
- [x] 1.5 GREEN: create `src/openkos/cli/observability.py` with `warn_if_walk_incomplete(bundle_dir: Path, *, mode: str = "warn", include_confidential: bool = False) -> None` — return early if `include_confidential`; emit STDERR line if `bool(okf._walk_errors(bundle_dir))` and `mode == "warn"`; raise `NotImplementedError` if `mode == "refuse"`. Use exact message text from design Decision 1. Not added to `sensitivity.py` (stays pure no-I/O leaf).
- [x] 1.6 REFACTOR: confirm no duplicate STDERR-formatting logic vs. `state/reindex.py:285` precedent; align docstring cross-reference.

## Phase 2: Signal Wiring — 5 Verbs

- [x] 2.1 RED: `tests/unit/cli/test_adjudicate.py` — `CliRunner`, monkeypatch `os.walk` onerror, assert STDERR substring present + exit 0 (template `test_reindex_cmd.py:86-125`); clean-bundle negative (empty STDERR); `--include-confidential` suppresses. (Note: this project's installed `click`/`typer` version separates stdout/stderr by default -- no `mix_stderr` constructor param exists; `CliRunner()` + `result.stderr` is the established project convention, confirmed via `test_query.py`.)
- [x] 2.2 RED: `tests/unit/cli/test_suggest_relations.py` — same 3 assertions (warn+exit0 / clean-silent / include-confidential-silent).
- [x] 2.3 RED: `tests/unit/cli/test_suggest_volatility.py` — same 3 assertions.
- [x] 2.4 RED: `tests/unit/cli/test_contradictions.py` — same 3 assertions.
- [x] 2.5 RED: `tests/unit/cli/test_query.py` — same 3 assertions (signal-only; query's leak path is already conformant, no `answer.py` change).
- [x] 2.6 GREEN: wire `observability.warn_if_walk_incomplete(layout.bundle_dir, include_confidential=include_confidential)` in `src/openkos/cli/main.py` after `llm = OllamaClient(model=cfg.model)`, before the verb call: adjudicate (~2848, before 2850-2855), suggest-relations (~2971, before 2973-2975), suggest-volatility (~3086, before 3088-3090), contradictions (~3217, before 3219-3224), query (~3481, before the vector/fts/graph index-cm block).
- [x] 2.7 REFACTOR: confirm identical call shape across all 5 sites; no per-verb drift in the helper invocation.

## Phase 3: Leak Closure — 4 Load Paths

- [x] 3.1 RED: `tests/unit/resolution/test_contradiction.py` — monkeypatch `sensitive_concept_ids` to return `frozenset()` (simulating a walk that missed the doc's subtree); place a confidential-on-disk doc reachable by direct path; capture `_load_doc`/`find_contradictions` output; assert the doc's body is absent (mirror `test_answer.py:2161`). Second case: `include_confidential=True` includes it. Covers spec "Confidential doc absent from the precomputed blocked set is caught at load" + "`--include-confidential` bypasses the re-check".
- [x] 3.2 RED: `tests/unit/resolution/test_adjudication.py` — same two cases against `_load_members`/`adjudicate_candidates`.
- [x] 3.3 RED: `tests/unit/resolution/test_edge_typing.py` — same two cases against `_load_doc`/`suggest_edge_types` (exercised through the `suggest_relations` orchestrator).
- [x] 3.4 RED: `tests/unit/resolution/test_volatility_typing.py` — same two cases against the volatility load path (per-doc frontmatter re-read after the `blocked` filter at line 187).
- [x] 3.5 GREEN: `src/openkos/resolution/contradiction.py` — add `not include_confidential and sensitivity.blocks_llm_send(metadata.get("sensitivity"))` guard in `_load_doc` (~203, after `load_frontmatter` ~216) → degrade to `(concept_id, "")`; thread `include_confidential` from `find_contradictions` (~397-398) call sites.
- [x] 3.6 GREEN: `src/openkos/resolution/edge_typing.py` — same guard in `_load_doc` (~130, after ~142) → degrade to `(concept_id, "")`; thread from `suggest_edge_types` (~211-212), and from `suggest_edge_types` into `suggest_relations`.
- [x] 3.7 GREEN: `src/openkos/resolution/adjudication.py` — same guard in `_load_members` (~90, after `load_frontmatter` ~106) → `continue` (existing skip contract); thread from `adjudicate_candidates` (~223).
- [x] 3.8 GREEN: `src/openkos/resolution/volatility_typing.py` — per-doc frontmatter re-read guard after the `blocked` filter (~187); `LintDoc` lacks a `sensitivity` field, so re-read frontmatter from disk and re-check `blocks_llm_send`, mirroring query's fresh re-read; thread `include_confidential` from `suggest_volatility` (~159).
- [x] 3.9 REFACTOR: confirm all 4 guards reuse the exact `answer.py:211-214` semantic verbatim (no drift in predicate or `include_confidential` short-circuit); confirm `sensitivity.py` and `answer.py` remain unmodified.

## Phase 4: Verification

- [x] 4.1 Run full suite: `uv run pytest tests/unit/cli/test_observability.py tests/unit/cli/test_query.py tests/unit/cli/test_contradictions.py tests/unit/cli/test_adjudicate.py tests/unit/cli/test_suggest_relations.py tests/unit/cli/test_suggest_volatility.py tests/unit/resolution/test_contradiction.py tests/unit/resolution/test_adjudication.py tests/unit/resolution/test_edge_typing.py tests/unit/resolution/test_volatility_typing.py` — all RED tests now GREEN. Whole-tree `uv run pytest`: 1699 passed (27 new tests: 4 observability unit + 15 CLI signal + 8 resolution leak-closure; baseline 1672).
- [x] 4.2 Confirm `sensitivity.py` diff is empty (pure leaf invariant held) and `answer.py` diff is empty (query already conformant, spec scenario "Query is already conformant"). Confirmed via `git diff --stat -- src/openkos/sensitivity.py src/openkos/retrieval/answer.py` (empty output).
- [x] 4.3 Update `design.md`/proposal cross-references if any line numbers shifted during implementation (documentation only, no behavior change). Actual insertion points matched the design's `~`-prefixed approximate line numbers within a few lines (single-statement insertions before each verb's library call); no correction needed. `suggest_edge_types`/`suggest_relations` additionally gained an `include_confidential` parameter each (not explicitly named in the design's per-line file table but implied by "thread `include_confidential`" in Decision 4 and task 3.6) — noted here as the one small design-vs-implementation delta.

### Whole-tree verification (arc lesson — CI runs whole-tree, not per-file)
- `uv run pytest`: 1699 passed
- `uv run mypy .`: Success: no issues found in 121 source files
- `uv run ruff check .`: All checks passed
- `uv run ruff format --check .`: 121 files already formatted
