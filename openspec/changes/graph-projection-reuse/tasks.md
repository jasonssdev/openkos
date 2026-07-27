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

- [x] 1.1 In `tests/unit/cli/test_suggest_relations.py`, `test_contradictions.py`, `test_status.py`, replace all 13 call sites of `_touch_vectors_db(tmp_path)` / `_write_nonempty_vectors_db(tmp_path)` with `seed_vectors_db(tmp_path)`, adding `seed_vectors_db: Callable[[Path], None]` to each affected test's signature. **RED**: `uv run pytest tests/unit/cli/` now fails with `fixture 'seed_vectors_db' not found` — proves the fixture is required and does not yet exist.

### Phase 2: GREEN

- [x] 2.1 Add the `seed_vectors_db` factory fixture to `tests/unit/cli/conftest.py` (imports: `sqlite3`, `Path`, `Callable` from `collections.abc`), per design §7, with `try/finally` around the connection.
- [x] 2.2 Delete `_touch_vectors_db` (`test_suggest_relations.py:74`), its twin (`test_contradictions.py:73`), and `_write_nonempty_vectors_db` (`test_status.py:40`); remove any now-unused `import sqlite3` per module.
- [x] 2.3 Run `uv run pytest tests/unit/cli/` — GREEN, no duplicate seeding helpers remain.

### Phase 3: Verify

- [x] 3.1 `ruff check` and `mypy --strict` clean on the 4 touched files.

---

## PR2 (#196 + #195) — `store` reuse, CLI restructuring, docstrings

### Phase 4: RED — reader contract tests (design §8a)

- [ ] 4.1 `tests/unit/graph/test_summary.py::test_graph_edge_summary_with_supplied_store_matches_own_build` — asserts `graph_edge_summary(bundle, store=s) == graph_edge_summary(bundle)`. **RED**: `TypeError: graph_edge_summary() got an unexpected keyword argument 'store'`.
- [ ] 4.2 `tests/unit/graph/test_summary.py::test_graph_edge_summary_does_not_close_supplied_store` — after the call, `store.edges()` still works. **RED**: same `TypeError` as 4.1 (keyword does not exist yet).
- [ ] 4.3 `tests/unit/resolution/test_edge_typing.py::test_candidate_edges_with_supplied_store_matches_own_build` and `::test_candidate_edges_does_not_close_supplied_store`. **RED**: `TypeError` on the `store=` keyword.
- [ ] 4.4 `tests/unit/resolution/test_edge_typing.py::test_candidate_edges_ignores_bundle_walk_when_store_supplied` — store built from bundle A, `bundle_dir` argument points at bundle B; returned edges come from A. **RED**: same `TypeError`.
- [ ] 4.5 `tests/unit/resolution/test_contradiction.py::test_find_contradictions_with_supplied_store_matches_own_build` and `::test_find_contradictions_does_not_close_supplied_store` (uses the existing fake `LLMBackend`). **RED**: `TypeError` on `store=`.

### Phase 5: GREEN — reader keyword + extraction

- [ ] 5.1 `src/openkos/graph/summary.py`: add `store: GraphStore | None = None` to `graph_edge_summary`; extract `_summarize(store) -> tuple[int,int]`; two-branch early return (no `nullcontext`, no shared helper — design §2/§3).
- [ ] 5.2 `src/openkos/resolution/edge_typing.py`: add `store` keyword to `candidate_edges`, last in the parameter list; extract `_edges_from(store) -> list[Edge]`; same two-branch shape.
- [ ] 5.3 `src/openkos/resolution/contradiction.py`: add `store` keyword to `find_contradictions`, last in the parameter list; extract `_pairs_and_types(store, excluded) -> tuple[list[Pair], int, dict[Pair, str]]`.
- [ ] 5.4 Run `uv run pytest tests/unit/graph/ tests/unit/resolution/` — 4.1-4.5 GREEN. Verify by grep: `store.close()` MUST NOT appear in any of the 3 files.

### Phase 6: RED — CLI one-build regression + accepted delta (design §8b/§8c)

- [ ] 6.1 `tests/unit/cli/test_suggest_relations.py::test_suggest_relations_builds_the_graph_once_on_the_zero_path` — pass-through counting wrapper patched at `openkos.cli.main.build_graph`, `openkos.graph.summary.build_graph`, `openkos.resolution.edge_typing.build_graph`; asserts `len(calls) == 1` plus the existing "No concept relationships..." output. **RED**: `len(calls) == 2` against current code (one build in `candidate_edges`, one in `_zero_edge_state_message` → `graph_edge_summary`).
- [ ] 6.2 `tests/unit/cli/test_suggest_relations.py::test_suggest_relations_builds_the_graph_once_on_the_non_zero_path` — guards the split-block refactor. **RED**: current code already builds once here, so write this test to additionally assert the store is closed before the LLM loop starts (new invariant) — fails until Phase 7's `suggest_relations_cmd` restructuring lands.
- [ ] 6.3 `tests/unit/cli/test_contradictions.py::test_contradictions_builds_the_graph_once_on_the_zero_path` — same shape as 6.1 for `contradictions`. **RED**: `len(calls) == 2`.
- [ ] 6.4 `tests/unit/cli/test_status.py::test_status_builds_the_graph_once` — same shape for `status`. **RED**: this one may already show `len(calls) == 1` (status has no redundant build per decision 4) — if so, this task instead pins the existing single-call behavior as a regression guard and is GREEN on write; do not force an artificial RED here.
- [ ] 6.5 `tests/unit/cli/test_suggest_relations.py::test_suggest_relations_all_excluded_message_counts_proximity_rows` — bundle with a confidential concept reachable only via a proximity row, seeded via `seed_vectors_db` with a real matching embedding; asserts the `all_excluded` wording carries the higher, candidates-inclusive `untyped` count. **RED**: current code reports the lower candidates-free count from the separate `graph_edge_summary(bundle_dir)` build.

### Phase 7: GREEN — CLI call-site restructuring

- [ ] 7.1 `src/openkos/cli/main.py`: `_zero_edge_state_message` signature — `store: GraphStore` becomes required (not `Optional`), per design §5. Add `from openkos.graph.base import GraphStore` import.
- [ ] 7.2 `src/openkos/cli/main.py`: add `from openkos.graph.sqlite_graph import build_graph` (grouped with the existing `graph.summary` import).
- [ ] 7.3 Restructure `suggest_relations_cmd` (replaces current `_open_proximity_or_degrade`/`candidate_edges`/zero-branch block) per design §4 shape: source-then-build prologue, `with graph as store:` wrapping `candidate_edges(..., store=store)` and the zero-branch `_zero_edge_state_message(..., store=store, use_typed_count=False, ...)`; everything from `if not auto:` onward moves outside the `with` block.
- [ ] 7.4 Restructure `contradictions` per design §4: same source-then-build prologue; delete the now-redundant `finally: source.close()`; `find_contradictions(..., store=store)` and the zero-branch `_zero_edge_state_message(..., store=store, use_typed_count=True, ...)` stay inside `with graph as store:`.
- [ ] 7.5 Restructure `status` per design §4: `with build_graph(layout.bundle_dir) as store: total, typed = graph_edge_summary(layout.bundle_dir, store=store)`.
- [ ] 7.6 Run `uv run pytest tests/unit/cli/` — 6.1-6.5 GREEN.

### Phase 8: Update pre-existing count assertion (intended expectation change, decision 1 — NOT a weakened test)

- [ ] 8.1 Re-verify at apply time whether `tests/unit/cli/test_suggest_relations.py::test_suggest_relations_post_relate_reports_excluded_untyped_rows_not_none_untyped` (currently ~line 244, asserts `"2 relation(s) exist; 1 untyped, ..."`) actually produces different counts once the shared candidates-seeded store lands — its bundle's `seed_vectors_db` embedding does not match its concepts today, so it may be unaffected. If the numbers change, update the hardcoded string to the new value with a comment noting **this is the accepted correctness fix from the proposal (shared-store zero-result counts), not a loosened assertion**. Scan the same file for any other hardcoded `untyped`/`relation(s) exist` counts and apply the same rule.

### Phase 9: Docstring corrections (decision 3 — four exact locations)

- [ ] 9.1 `src/openkos/resolution/edge_typing.py:25-28` (module docstring) — replace with the corrected layering text (design §9a): `cli/main.py` importing `openkos.graph` is NOT a violation.
- [ ] 9.2 `src/openkos/resolution/contradiction.py:25-28` (module docstring) — same replacement, `find_contradictions` substituted for `candidate_edges`.
- [ ] 9.3 `src/openkos/cli/main.py:4912-4915` (`suggest_relations_cmd` docstring) — replace the false "never openkos.graph directly" claim per design §9d.
- [ ] 9.4 `src/openkos/cli/main.py:5217-5219` (`contradictions` docstring) — same replacement per design §9d.
- [ ] 9.5 `src/openkos/cli/main.py:4312-4314` (`status` docstring) — replace the false "scans bundle ONCE" claim with the three-independent-walks wording (design §4, "Shape — status").
- [ ] 9.6 `src/openkos/resolution/edge_typing.py:298-299` — replace the stale `candidate_edges` docstring per design §9b.

### Phase 10: Final verification

- [ ] 10.1 `mypy --strict` clean across all touched files — zero `# type: ignore` (design §6: none expected).
- [ ] 10.2 `ruff check` clean across all touched files.
- [ ] 10.3 `uv run pytest` full suite green; `uv run pytest --cov` at or above the 90% branch gate (the new `if store is not None: return ...` branches are covered by Phase 4/8 supplied-store tests plus every existing default-`None` call site — design §8d).
- [ ] 10.4 Grep confirms `open_graph_store_readonly` still appears only at `cli/main.py:5458` (`query`) — the on-disk `.openkos/graph.db` path is untouched (non-goal 5).
- [ ] 10.5 Comment on issue #196 explaining the `suggest-relations` zero-result count change and its rationale when PR2 merges.

---

## Threat Matrix

N/A per design — no routing, shell, subprocess, VCS/PR automation, or process-integration boundary in this change.
