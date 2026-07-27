# Tasks: Graph Projection Reuse Within One CLI Invocation

Issues: #197 (PR1), #196 + #195 (PR2). Strict TDD: every slice is RED before GREEN.

> **Line-number drift warning**: every file:line reference below is copied from
> `design.md`'s "verified at design time" table. `sdd-apply` MUST re-read each
> symbol by name before editing — do not trust the numbers blindly, the tree
> may have moved since design.

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | PR1 ~130, PR2 ~340, total ~470 |
| 400-line budget risk | PR1: Low, PR2: Medium |
| Chained PRs recommended | Yes |
| Suggested split | PR1 (#197) → PR2 (#196+#195), Feature Branch Chain, PR2 targets PR1's branch |
| Delivery strategy | auto-forecast |
| Chain strategy | feature-branch-chain |

```text
Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: Medium
```

### Suggested Work Units

| Unit | Goal | PR | Focused test command | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| 1 | Shared `seed_vectors_db` fixture, 3 helpers deleted | PR1 | `uv run pytest tests/unit/cli/` | N/A — pure test refactor, no runtime behavior | Revert `conftest.py` + 3 test files; restores per-module helpers |
| 2 | `store` keyword on 3 readers + CLI call-site reuse + docstrings | PR2 | `uv run pytest tests/unit/graph/ tests/unit/resolution/ tests/unit/cli/` | `openkos suggest-relations` / `openkos contradictions` / `openkos status` on a seeded workspace | Revert `graph/summary.py`, `resolution/edge_typing.py`, `resolution/contradiction.py`, `cli/main.py`; keyword has no external consumers |

---

## PR1 (#197) — Shared vectors.db fixture

### Phase 1: RED

- [x] 1.1 In `tests/unit/cli/test_suggest_relations.py`, `test_contradictions.py`, `test_status.py`, replace all 14 call sites of `_touch_vectors_db(tmp_path)` / `_write_nonempty_vectors_db(tmp_path)` with `seed_vectors_db(tmp_path)`, adding `seed_vectors_db: Callable[[Path], None]` to each affected test's signature. **RED**: `uv run pytest tests/unit/cli/` now fails with `fixture 'seed_vectors_db' not found` — proves the fixture is required and does not yet exist.

### Phase 2: GREEN

- [x] 2.1 Add the `seed_vectors_db` factory fixture to `tests/unit/cli/conftest.py` (imports: `sqlite3`, `Path`, `Callable` from `collections.abc`), per design §7, with `try/finally` around the connection.
- [x] 2.2 Delete `_touch_vectors_db` (`test_suggest_relations.py:74`), its twin (`test_contradictions.py:73`), and `_write_nonempty_vectors_db` (`test_status.py:40`); remove any now-unused `import sqlite3` per module.
- [x] 2.3 Run `uv run pytest tests/unit/cli/` — GREEN, no duplicate seeding helpers remain.

### Phase 3: Verify

- [x] 3.1 `ruff check` and `mypy --strict` clean on the 4 touched files.

---

## PR2 (#196 + #195) — `store` reuse, CLI restructuring, docstrings

### Phase 4: RED — reader contract tests (design §8a)

- [x] 4.1 `tests/unit/graph/test_summary.py::test_graph_edge_summary_with_supplied_store_matches_own_build` — asserts `graph_edge_summary(bundle, store=s) == graph_edge_summary(bundle)`. **RED**: `TypeError: graph_edge_summary() got an unexpected keyword argument 'store'`.
- [x] 4.2 `tests/unit/graph/test_summary.py::test_graph_edge_summary_does_not_close_supplied_store` — after the call, `store.edges()` still works. **RED**: same `TypeError` as 4.1 (keyword does not exist yet).
- [x] 4.3 `tests/unit/resolution/test_edge_typing.py::test_candidate_edges_with_supplied_store_matches_own_build` and `::test_candidate_edges_does_not_close_supplied_store`. **RED**: `TypeError` on the `store=` keyword.
- [x] 4.4 `tests/unit/resolution/test_edge_typing.py::test_candidate_edges_ignores_bundle_walk_when_store_supplied` — store built from bundle A, `bundle_dir` argument points at bundle B; returned edges come from A. **RED**: same `TypeError`.
- [x] 4.5 `tests/unit/resolution/test_contradiction.py::test_find_contradictions_with_supplied_store_matches_own_build` and `::test_find_contradictions_does_not_close_supplied_store` (uses the existing fake `LLMBackend`). **RED**: `TypeError` on `store=`.

### Phase 5: GREEN — reader keyword + extraction

- [x] 5.1 `src/openkos/graph/summary.py`: add `store: GraphStore | None = None` to `graph_edge_summary`; extract `_summarize(store) -> tuple[int,int]`; two-branch early return (no `nullcontext`, no shared helper — design §2/§3).
- [x] 5.2 `src/openkos/resolution/edge_typing.py`: add `store` keyword to `candidate_edges`, last in the parameter list; extract `_edges_from(store) -> list[Edge]`; same two-branch shape.
- [x] 5.3 `src/openkos/resolution/contradiction.py`: add `store` keyword to `find_contradictions`, last in the parameter list; extract `_pairs_and_types(store, excluded) -> tuple[list[Pair], int, dict[Pair, str]]`.
- [x] 5.4 Run `uv run pytest tests/unit/graph/ tests/unit/resolution/` — 4.1-4.5 GREEN. Verify by grep: `store.close()` MUST NOT appear in any of the 3 files.

### Phase 6: RED — CLI one-build regression + accepted delta (design §8b/§8c)

- [x] 6.1 `tests/unit/cli/test_suggest_relations.py::test_suggest_relations_builds_the_graph_once_on_the_zero_path` — pass-through counting wrapper patched at `openkos.cli.main.build_graph`, `openkos.graph.summary.build_graph`, `openkos.resolution.edge_typing.build_graph`; asserts `len(calls) == 1` plus the existing "No concept relationships..." output. **RED**: `AttributeError: 'module' object at openkos.cli.main has no attribute 'build_graph'` (the import doesn't exist yet, an even stronger RED signal than the design's forecast `len(calls) == 2`).
- [x] 6.2 `tests/unit/cli/test_suggest_relations.py::test_suggest_relations_builds_the_graph_once_on_the_non_zero_path` — guards the split-block refactor; additionally asserts the store is closed before the LLM loop starts (new invariant). **RED**: same `AttributeError` as 6.1.
- [x] 6.3 `tests/unit/cli/test_contradictions.py::test_contradictions_builds_the_graph_once_on_the_zero_path` — same shape as 6.1 for `contradictions`. **RED**: same `AttributeError`.
- [x] 6.4 `tests/unit/cli/test_status.py::test_status_builds_the_graph_once` — same shape for `status`, pinning the existing single-call behavior as a regression guard (status has no redundant build per decision 4). **RED**: same `AttributeError` (the counting seam itself doesn't exist yet, even though the call count would already be 1 once the import lands).
- [x] 6.5 `tests/unit/cli/test_suggest_relations.py::test_suggest_relations_all_excluded_message_counts_proximity_rows` — bundle with a confidential concept reachable only via a proximity row. **Deviation from design**: seeded via a stub `CandidateSource` patched at `_open_proximity_or_degrade` (mirrors the existing `_Source`/`_StubCandidateSource` patterns already used elsewhere in this suite and in `test_edge_typing.py`), not a real sqlite-vec embedding — avoids a real Ollama/embedding dependency for a unit test while still exercising the identical `build_graph(candidates=...)` code path. **RED**: current code reports "No concept relationships in the graph yet." (the lower candidates-free count from the separate `graph_edge_summary(bundle_dir)` build), not the expected `all_excluded` wording.

### Phase 7: GREEN — CLI call-site restructuring

- [x] 7.1 `src/openkos/cli/main.py`: `_zero_edge_state_message` signature — `store: GraphStore` becomes required (not `Optional`), per design §5. `from openkos.graph.base import GraphStore` was already imported (line 34, predates this change) — no new import needed here.
- [x] 7.2 `src/openkos/cli/main.py`: add `from openkos.graph.sqlite_graph import build_graph` (grouped with the existing `graph.summary` import at line 35).
- [x] 7.3 Restructure `suggest_relations_cmd` (replaces current `_open_proximity_or_degrade`/`candidate_edges`/zero-branch block) per design §4 shape: source-then-build prologue, `with graph as store:` wrapping `candidate_edges(..., store=store)` and the zero-branch `_zero_edge_state_message(..., store=store, use_typed_count=False, ...)`; everything from `if not auto:` onward moves outside the `with` block.
- [x] 7.4 Restructure `contradictions` per design §4: same source-then-build prologue; delete the now-redundant `finally: source.close()`; `find_contradictions(..., store=store)` and the zero-branch `_zero_edge_state_message(..., store=store, use_typed_count=True, ...)` stay inside `with graph as store:`.
- [x] 7.5 Restructure `status` per design §4: `with build_graph(layout.bundle_dir) as store: total, typed = graph_edge_summary(layout.bundle_dir, store=store)`.
- [x] 7.6 Run `uv run pytest tests/unit/cli/` — 6.1-6.5 GREEN.

### Phase 8: Update pre-existing count assertion (intended expectation change, decision 1 — NOT a weakened test)

- [x] 8.1 Re-verified at apply time: `tests/unit/cli/test_suggest_relations.py::test_suggest_relations_post_relate_reports_excluded_untyped_rows_not_none_untyped` (asserts `"2 relation(s) exist; 1 untyped, ..."`) is **UNAFFECTED** — confirmed by running it unmodified after the Phase 7 restructuring landed (still PASSES, unchanged numbers `2`/`1`). Its bundle's `seed_vectors_db` embedding does not match either concept's real embedding, so the candidates-seeded store produces zero proximity rows over that bundle — no delta fires. Added a docstring note cross-referencing the NEW test that DOES exercise the delta (`test_suggest_relations_all_excluded_message_counts_proximity_rows`, task 6.5), so a future reader isn't left wondering why this one didn't move. Grepped the same file (`test_suggest_relations.py`) and `test_contradictions.py` for every other hardcoded `untyped`/`relation(s) exist`/`typed relation(s)` count: all other occurrences are on the `use_typed_count=True` (`contradictions`) path, which the delta spec's own second requirement pins as unaffected (proximity rows are never typed), or are unrelated (cap-reached line, docstrings). **No other assertion changed.**

### Phase 9: Docstring corrections (decision 3 — four exact locations)

- [x] 9.1 `src/openkos/resolution/edge_typing.py:25-28` (module docstring) — replace with the corrected layering text (design §9a): `cli/main.py` importing `openkos.graph` is NOT a violation.
- [x] 9.2 `src/openkos/resolution/contradiction.py:25-28` (module docstring) — same replacement, `find_contradictions` substituted for `candidate_edges`.
- [x] 9.3 `src/openkos/cli/main.py` `suggest_relations_cmd` docstring (verified at apply time at line ~4921, drifted from the design's `4912-4915` estimate) — replaced the false "never openkos.graph directly" claim per design §9d.
- [x] 9.4 `src/openkos/cli/main.py` `contradictions` docstring (verified at apply time at line ~5245, drifted from the design's `5217-5219` estimate) — same replacement per design §9d.
- [x] 9.5 `src/openkos/cli/main.py` `status` docstring (verified at apply time at line ~4313, matches the design's `4312-4314` estimate) — replaced the false "scans bundle ONCE" claim with the three-independent-walks wording (design §4, "Shape — status").
- [x] 9.6 `src/openkos/resolution/edge_typing.py` `candidate_edges` docstring — folded into the Phase 5.2 GREEN edit (the design §2/§9b wording was written directly into the new keyword-adding docstring in one pass, rather than as a separate later edit) per design §9b.

### Phase 10: Final verification

- [x] 10.1 `mypy --strict` clean across all touched files (`uv run mypy .` — 141 source files, 0 errors) — zero `# type: ignore` in any PRODUCTION file (design §6: none expected; confirmed). Test-file helper wrappers needed explicit `CandidateSource | None`/`SqliteGraphStore` return annotations instead of `**kwargs: object` to satisfy `--strict`, but no `# type: ignore` was needed anywhere, including tests.
- [x] 10.2 `ruff check` clean across all touched files (`uv run ruff check .` — all checks passed; `uv run ruff format --check .` — 141 files formatted).
- [x] 10.3 `uv run pytest` full suite green (2254 passed); `uv run pytest --cov` at 97.58% branch (gate 90%) — the new `if store is not None: return ...` branches are covered by Phase 4/8 supplied-store tests plus every existing default-`None` call site (design §8d).
- [x] 10.4 Grep confirms `open_graph_store_readonly` still appears only inside `_open_graph_or_degrade` (`cli/main.py`, used by `query`) — the on-disk `.openkos/graph.db` path is untouched (non-goal 5). Line number drifted from the design's `5458` estimate to `5511` (verified at apply time); the constraint itself (single call site) holds.
- [ ] 10.5 Comment on issue #196 explaining the `suggest-relations` zero-result count change and its rationale when PR2 merges. **Not performed by sdd-apply** — this is a GitHub-issue-comment action, out of scope for the code-implementation executor; the orchestrator/maintainer should post it when PR2 merges, using the rationale captured in this file's Phase 8 note and design.md §4.

---

## Threat Matrix

N/A per design — no routing, shell, subprocess, VCS/PR automation, or process-integration boundary in this change.
