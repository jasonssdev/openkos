# Proposal: Graph Projection Reuse Within One CLI Invocation

Issues: #197, #196, #195 (landing order).

## Intent

`build_graph` walks every concept doc in the bundle and rebuilds an in-memory SQLite projection on each call. Three CLI commands pay that walk more than once per invocation, or pay it while claiming they do not:

- `openkos suggest-relations` and `openkos contradictions`: on the **zero-result path** they build the projection twice — once in `candidate_edges` / `find_contradictions`, then again inside `_zero_edge_state_message` → `graph_edge_summary`. Users with nothing to suggest wait twice as long for a "nothing to do" message (#196).
- `openkos status`: rebuilds the whole projection every run to print one edge-state line (#195).
- Test suites: three divergent copies of the same `vectors.db`-seeding helper across `test_suggest_relations.py`, `test_contradictions.py`, `test_status.py` (#197).

Cost scales with bundle size and is felt by every daily `status` / `suggest-relations` user on a growing bundle.

## Scope

### In Scope

| # | Deliverable | Honest payoff |
|---|---|---|
| 197 | Move `vectors.db` seeding into one `tests/unit/cli/conftest.py` fixture; delete the three helpers | Enables #196/#195 tests to be written against one fixture instead of diverging a fourth copy |
| 196 | Additive `store: GraphStore \| None = None` keyword on `graph_edge_summary`, `candidate_edges`, `find_contradictions`; `suggest_relations_cmd` / `contradictions` open one store per invocation and pass it down | **Real fix.** Removes a genuine second full projection build on the zero-result path |
| 196 | **Correct `suggest-relations`' zero-result counts** — they are currently computed over a different projection than the filtering ran over | **Correctness fix.** Intended observable output change — see below |
| 195 | `status` opens the store once and passes it to `graph_edge_summary`; correct `status`'s false "scans the bundle ONCE" docstring | **Little to no runtime win** — see below |
| — | Correct **four** stale layering docstrings: `resolution/edge_typing.py:25-28`, `resolution/contradiction.py:25-28`, `cli/main.py:4912-4915`, `cli/main.py:5217-5219` | Removes actively misleading guidance |

### Intended output change in `suggest-relations` (accepted by the maintainer)

`suggest_relations_cmd` calls `candidate_edges(bundle_dir, candidates=source)` (`cli/main.py:4980-4984`), which builds the projection **with** proximity candidates. Its zero-result branch then calls `_zero_edge_state_message`, which calls `graph_edge_summary(layout.bundle_dir)` (`cli/main.py:4882`) — and `graph/summary.py:55` builds **without** candidates. A proximity-nominated pair becomes one row with `relation_type = NULL` (`graph/sqlite_graph.py:45`), i.e. it counts as untyped.

So the `all_excluded` message asserts "every untyped pair is already typed elsewhere or filtered as confidential" while counting rows from a **different projection** than the one the filtering ran over. That inconsistency exists today and is the real defect. Sharing one store corrects it; it is not a regression introduced by this change.

Consequence, split by command:

| Command | Count read | Effect |
|---|---|---|
| `contradictions` | `use_typed_count=True` → `typed` | Unaffected; output stays byte-identical |
| `status` | edge-state line, no candidates | Unaffected; output stays byte-identical |
| `suggest-relations` | `use_typed_count=False` → `total` / `untyped` | **Numbers in the zero-result message will change. Intended.** Requires a dedicated test |

When PR2 lands, comment on issue **#196** explaining the counted-numbers change and its rationale.

### #195: the issue's premise is wrong, stated plainly

The issue title is right — `status` does rebuild the whole projection every run. The issue **body** is wrong: it claims `status` walks the bundle "twice" and frames it as a double *build*. Exploration verified `status` performs **three** independent `_iter_docs` walks (`survey_bundle`, `lint_check.collect_docs`, `graph_edge_summary`'s `build_graph`) and exactly **one** `build_graph` call. There is no redundant build inside `status` to remove.

So under this change #195 delivers: parameter plumbing (`status` owns the store lifetime, consistent with the other two commands) and an honest docstring. It does **not** make `status` faster and does **not** make it a single-walk command. Truly closing #195's spirit means consolidating `survey_bundle` + `collect_docs` into one walk — a materially larger refactor across the canonical layer that the exploration recommends against, and that no spec requires. We land the plumbing plus the docstring correction, and defer walk consolidation as a separate change.

### Out of Scope (non-goals)

- **Reading the persisted on-disk `.openkos/graph.db`** instead of rebuilding. That belongs to `derived-index-cache` and would change staleness semantics for commands that deliberately need live truth without a prior `reindex`.
- **Consolidating `survey_bundle` / `collect_docs` into a single walk** in `status`.
- Any change to `find_contradictions` / `candidate_edges` return shapes (~30+ test call sites unpack the current 2-tuple).
- Cross-invocation caching of the projection — `build_graph` stays rebuild-per-run.
- Preserving byte-identical output **across the board**. `contradictions` and `status` output MUST NOT change; `suggest-relations`' zero-result counts are intended to change (see above). Do not add a guard that pins the old `suggest-relations` numbers.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `graph-projection`: ADD a requirement that a caller MAY supply an already-open `GraphStore` to derived-layer readers within one invocation, and that results MUST be identical to the reader opening its own projection. Rebuild-per-run for `build_graph` itself is unchanged.

No spec change for `status` or `contradiction-detection`: no requirement pins walk counts, their observable behavior is unchanged, and the new keyword is a behavior-preserving implementation detail.

`candidate-edge-seeding`: sdd-spec MUST check whether any scenario pins the `suggest-relations` zero-result counts. If one does, it needs a MODIFIED delta stating the counts are computed over the same projection the candidate filtering ran over. If none does, no delta is required.

## Approach

Adopt the exploration's recommended **b-refined**: additive optional `store` keyword. When omitted (every existing call site) the reader's behavior is byte-identical to today. Callers that pass a store own its lifetime; the reader skips its own `with build_graph(...)` and operates on the supplied store. `cli/main.py` opens `build_graph(...)` once per invocation and threads it through.

Because `suggest-relations` opens its single store **with** proximity candidates, sharing it also aligns the zero-result counts with the projection the filtering ran over — the intended correctness fix described in Scope.

Rejected alternative: returning `(total, typed)` counts alongside results. It breaks `find_contradictions`'s return contract at ~30+ unrelated test sites, requires the readers to compute a *second*, differently-filtered result internally anyway, and does nothing for `status`.

Layering is legal: the canonical-layer guard scopes to `model`/`bundle`/`state`; `cli/main.py` already imports `openkos.graph` (`main.py:33-35`) and already holds open graph stores for `query` and `reindex`. Four docstrings claim otherwise and are corrected here — `resolution/edge_typing.py:25-28` and `resolution/contradiction.py:25-28` ("cli/main.py MUST NOT import openkos.graph directly"), plus `cli/main.py:4912-4915` and `cli/main.py:5217-5219`, which claim the module "never imports openkos.graph directly" — false as of `cli/main.py:33-35`.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/graph/summary.py` | Modified | `graph_edge_summary` gains optional `store` |
| `src/openkos/resolution/edge_typing.py` | Modified | `candidate_edges` gains optional `store`; stale docstring corrected |
| `src/openkos/resolution/contradiction.py` | Modified | `find_contradictions` gains optional `store`; stale docstring corrected |
| `src/openkos/cli/main.py` | Modified | `suggest_relations_cmd`, `contradictions`, `status`, `_zero_edge_state_message` open/thread one store; `status` docstring plus two stale layering docstrings corrected |
| `tests/unit/cli/conftest.py` | Modified | New shared `vectors.db` fixture |
| `tests/unit/cli/test_{suggest_relations,contradictions,status}.py` | Modified | Use the fixture; add single-build regression assertions; new test for `suggest-relations`' corrected zero-result counts |

## Delivery

Strict TDD (`uv run pytest`). Estimated ~350-450 changed lines total. Fits the 800-line session budget, but exceeds the 400-line per-PR review budget when combined, and #197 must land first regardless.

**Recommend 2 chained PRs:**

1. **PR1 — #197** (~80-110 lines): shared conftest fixture, three helpers deleted. Autonomous; green suite is its own verification.
2. **PR2 — #196 + #195** (~250-340 lines), targeting PR1's branch: `store` keyword, CLI call-site restructuring, docstring corrections, single-build regression tests.

`Decision needed before apply: No` — chaining is the recommendation and both slices sit under 400.

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Design/apply drifts into reading on-disk `graph.db` | Med | Explicit non-goal above; verify no `open_graph_store_readonly` appears outside `query` |
| Store lifetime bug — reader used after the CLI's `with` block closes | Med | Keep all reader calls inside the single `with build_graph(...)` block; add a test asserting one build per invocation |
| Signature change ripples into ~30+ existing test call sites | Low | Keyword is optional and defaults to today's behavior; no existing call site changes |
| Issue line numbers have all drifted from the current tree | High | Re-verify locations at apply time; do not copy issue line refs forward |
| #195 closes with less than the issue body implies | High | Stated explicitly above; note it on the issue when closing |
| Reviewer reads the `suggest-relations` count change as an unintended regression | Med | It is an accepted, intended correctness fix (see Scope); cover it with a dedicated test and comment the rationale on #196 when PR2 lands |

## Rollback Plan

Both slices are additive and default-preserving. Revert PR2 to restore per-reader `build_graph` ownership with zero API breakage (the `store` keyword has no external consumers). Revert PR1 independently to restore the per-module helpers. No data, schema, or on-disk artifact is touched.

## Dependencies

None external. PR2 depends on PR1 landing first.

## Success Criteria

- [ ] `suggest-relations` and `contradictions` call `build_graph` exactly once per invocation, including the zero-result path
- [ ] `contradictions` output is unchanged (it reads `typed`, which the shared store does not affect)
- [ ] `status` calls `build_graph` exactly once, its output is unchanged, and its docstring accurately describes its walks
- [ ] `suggest-relations`' zero-result counts are computed over the same candidates-included projection the filtering ran over. The printed numbers MAY differ from today; this is intended and MUST be covered by a dedicated test asserting the corrected counts
- [ ] Issue #196 carries a comment explaining the counted-numbers change when PR2 lands
- [ ] `graph_edge_summary` / `candidate_edges` / `find_contradictions` behave identically when `store` is omitted; no existing call site changed
- [ ] One `vectors.db` fixture in `tests/unit/cli/conftest.py`; zero duplicate helpers
- [ ] All four stale layering docstrings corrected; no "MUST NOT import openkos.graph" or "never imports openkos.graph directly" claim about `cli/main.py` remains
- [ ] `uv run pytest`, `ruff`, `mypy` green; coverage >= 90%

## Open Questions

1. Should #195 be closed by this change with the correction noted, or retitled/re-scoped as a follow-up for true single-walk `status`? (Default assumption: close it, comment the correction, open a follow-up only if the walk cost is measured to matter.)
2. Preferred keyword name — `store` vs. `graph_store`? (Default: `store`; design phase may settle it.)
