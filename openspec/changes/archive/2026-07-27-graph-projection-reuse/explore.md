# Exploration: graph-projection-reuse (issues #195, #196, #197)

## Current State

**Call graph today (verified against working tree, 2026-07-27):**

- `okf._iter_docs(bundle_dir)` (`src/openkos/model/okf.py:833`) is the canonical doc-walk generator every reader ultimately calls.
- `okf.survey_bundle(bundle_dir)` (`okf.py:908`) — one walk, produces source/concept counts + §9 findings. Canonical layer, no graph knowledge.
- `sqlite_graph._populate_graph_tables` (`src/openkos/graph/sqlite_graph.py:286`) calls `okf._iter_docs(bundle_dir)` at **line 325** (not 271 as the issue states — drift) to build nodes/edges (3 passes: untyped links, typed `relations:`, embedding-proximity candidates).
- `sqlite_graph.build_graph(bundle_dir, *, candidates=None)` (`sqlite_graph.py:416`) opens `sqlite3(":memory:")`, delegates to `_populate_graph_tables`, returns a context-managed `SqliteGraphStore`. Rebuild-per-call by contract (`graph-projection` spec).
- `graph.summary.graph_edge_summary(bundle_dir)` (`src/openkos/graph/summary.py:46`, confirmed at **line 55**: `with build_graph(bundle_dir) as store:`) is a thin wrapper: opens its own `build_graph`, filters to concept-to-concept edges, returns `(total, typed)`, closes.
- `resolution.edge_typing.candidate_edges(bundle_dir, ...)` (`src/openkos/resolution/edge_typing.py:282`) opens **its own** `with build_graph(bundle_dir, candidates=candidates) as store:` (line 319), filters to the untyped/not-typed-elsewhere/non-confidential set, returns `list[Edge]`.
- `resolution.contradiction.find_contradictions(bundle_dir, ...)` (`src/openkos/resolution/contradiction.py:381`) likewise opens its own `build_graph` internally (mirrors `candidate_edges`), returns `(list[ContradictionVerdict], total_pair_count)`.
- `cli/main.py::_zero_edge_state_message` (definition now at **lines 4840-4890**, NOT 4762-4765) is called from BOTH `suggest_relations_cmd`'s zero branch and `contradictions`'s zero branch. It calls `graph_edge_summary(layout.bundle_dir)` (line 4881) whenever `embeddings_missing` is `False`.

**Confirmed double-build on the zero-result path (#196):**
`suggest_relations_cmd` calls `candidate_edges(...)` at **lines 4980-4984** (issue said 4859-4861 — drift), which opens/closes `build_graph` once. If `edges` is empty, it calls `_zero_edge_state_message(...)` at **lines 4993-5005**, which calls `graph_edge_summary` → a SECOND `build_graph` open/close over the identical bundle state. Same pattern in `contradictions` (`find_contradictions` call at **lines 5281-5287**, issue said 5147-5152 — drift; zero-branch call at **lines 5320-5329**).

**Status (#195):** `status()` (`cli/main.py:4303`) calls `okf.survey_bundle` at **line 4337** (issue said 4263 — drift), `lint_check.collect_docs(layout.bundle_dir)` at **line 4369** (a THIRD independent doc walk, not mentioned in the issue text at all), and — when `vectors.db` is non-empty — `graph_edge_summary(layout.bundle_dir)` at **line 4396**, which is the ONE `build_graph` call. So `status` today does not call `build_graph` twice; it does **three separate `_iter_docs` walks total** (`survey_bundle`, `collect_docs`, `build_graph`), of which only `build_graph`'s is graph-projection-specific. The docstring's "scans the bundle ONCE via `survey_bundle`" claim (line ~4313) is already false today independent of any graph work, because of `collect_docs` alone.

## Affected Areas

- `src/openkos/graph/summary.py` — `graph_edge_summary` needs an opt-in "reuse an already-open store" path.
- `src/openkos/resolution/edge_typing.py` — `candidate_edges` owns its own `build_graph` open/close; needs the same opt-in path.
- `src/openkos/resolution/contradiction.py` — `find_contradictions`, same.
- `src/openkos/cli/main.py` — `suggest_relations_cmd`, `contradictions`, `status`, `_zero_edge_state_message` — call-site restructuring to open one store per invocation where applicable.
- `tests/unit/cli/test_suggest_relations.py`, `tests/unit/cli/test_contradictions.py`, `tests/unit/cli/test_status.py` — each has its own `.openkos/vectors.db` stub-seeding helper (verified still present, still divergent — see below); these are exactly the modules whose CLI-level tests will need updates for the reuse behavior.
- `tests/unit/cli/conftest.py` — exists today (only holds `_offline_ollama_by_default`), has NO shared vectors.db fixture yet; #197's target.
- `openspec/specs/graph-projection/spec.md`, `openspec/specs/status/spec.md`, `openspec/specs/candidate-edge-seeding/spec.md`, `openspec/specs/contradiction-detection/spec.md` — candidates for amendment (see below).

## Layering Constraint — the decisive finding

The issue's framing quotes `edge_typing.py`'s docstring ("cli/main.py MUST NOT import `openkos.graph` directly ... design D2/D6") as if it were still the live constraint. **It is stale.** The actual, currently-enforced constraint lives in `tests/unit/graph/test_analysis.py::test_cli_main_registers_no_graph_command` (lines 193-223), whose own comment is explicit:

> "`cli/main.py` importing `openkos.graph` directly is NOT itself a spec violation (the requirement's canonical-layer import guard scopes to `model`/`bundle`/`state`, covered separately by `test_base.py::test_canonical_layer_does_not_import_graph`); Slice 5 PR2's `reindex` command legitimately imports `openkos.graph.sqlite_graph` ... mirroring how `query` legitimately imports `state.fts`."

And `openspec/specs/graph-projection/spec.md`'s own "No CLI Surface, No Canonical-Layer Import" requirement text: "`graph/` MUST NOT introduce a CLI command ... and MUST NOT be imported by `model`, `bundle`, or `state`." No mention of `cli/main.py` being forbidden to import it.

Confirmed in the current tree: `cli/main.py:35` already does `from openkos.graph.summary import graph_edge_summary`, `cli/main.py:2249` calls `sqlite_graph.reindex_graph(...)` directly, and `cli/main.py:5458` calls `sqlite_graph.open_graph_store_readonly(...)` directly for `query`. The real, live rule is narrower: (1) canonical layer (`model`/`bundle`/`state`) must never import `openkos.graph`; (2) no `graph` CLI verb is ever registered. `cli/main.py` importing `openkos.graph` (or its submodules) directly is already established practice, not a violation.

This means option (b) — building the projection once in `cli/main.py` and sharing the open store — is **not** layering-forbidden. The resolution-layer docstrings' "encapsulates the `openkos.graph` read" language describes a real, still-good API-design choice (keeps `candidate_edges`/`find_contradictions` self-contained, testable without a CLI), but it is a design preference, not an enforced boundary.

## Approaches for #196/#195

1. **(a) Return `(total, typed)` counts alongside results** — smaller change.
   - Pros: no store lifetime management in `cli/main.py`; each function stays fully self-contained.
   - Cons: **breaks the existing return-shape contract.** `find_contradictions` already returns `(verdicts, total_pair_count)` and is unpacked as a 2-tuple in **~30 call sites** in `tests/unit/resolution/test_contradiction.py` alone. Widening it to a 3-tuple (or a dataclass) breaks every one of those sites for a change unrelated to what they're testing. Also: `candidate_edges`'s filtered list and `graph_edge_summary`'s raw (total, typed) are computed with genuinely different filters (pair-level exclusion + confidentiality vs. raw concept-edge counts) — `_zero_edge_state_message`'s own docstring depends on this distinction (the `all_excluded` branch). So (a) cannot just "count what candidate_edges already returned"; it would need `candidate_edges`/`find_contradictions` to independently compute a *second* result inside their existing `with build_graph(...) as store:` block and return it too — feasible, but the return-shape breakage above is the real cost.
   - Also **does not touch #195 at all**: `status` never calls `candidate_edges` or `find_contradictions`.
   - Effort: Medium (once the ~30-site test breakage is counted).

2. **(b) Build once, share the open store** — general, fixes both.
   - Pros: eliminates the true double-build in `suggest-relations`/`contradictions` zero-paths (#196) AND `status`'s one (arguably sole) graph-specific rebuild (#195, though see the 3-walk caveat below); layering-clean per the corrected constraint above.
   - Cons/risk if done as "cli/main.py holds a raw open store and calls into resolution internals directly": would leak `_candidate_edges`/`_candidate_pairs`/sensitivity-filtering logic out of the resolution layer and into `cli/main.py`, duplicating logic that currently lives once.
   - Effort: Medium-High if done naively; Low-Medium with the refinement below.

3. **(b-refined, recommended) Optional pre-opened store as an additive keyword parameter.**
   Give `candidate_edges`, `find_contradictions`, and `graph_edge_summary` an **optional** `store: GraphStore | None = None` keyword (name TBD in design). When `None` (the default — every existing call site, all ~30+ test call sites included), behavior is byte-identical to today: each function opens and closes its own `build_graph`. When a caller supplies an already-open store, the function skips its own `with build_graph(...)` and operates directly on the supplied store, leaving open/close ownership with the caller.

   `cli/main.py`'s `suggest_relations_cmd`/`contradictions` then open `with build_graph(layout.bundle_dir, candidates=source) as store:` **once**, pass `store=store` into `candidate_edges`/`find_contradictions`, and — only on the zero-result branch — also pass `store=store` into `graph_edge_summary`, replacing the current call that triggers a second internal `build_graph`. `status` does the same: opens `build_graph(layout.bundle_dir)` once (when `vectors.db` is non-empty), passes it into `graph_edge_summary(layout.bundle_dir, store=store)`.

   - Pros: zero breakage of existing signatures/return shapes (fully additive, default-preserving); fixes #196 and the one graph-specific rebuild in #195; keeps the resolution layer's internal filtering logic in one place (no duplication in `cli/main.py`); each function's own docstring's "encapsulates the `openkos.graph` read" claim stays essentially true (it still owns the *logic*, just not unconditionally the *lifecycle*).
   - Cons: touches three function signatures and two CLI command bodies (context-manager restructuring); does not by itself make `status` do a truly single bundle walk — see below.
   - Effort: Low-Medium.

**Recommendation: 3 (b-refined).** It gets the generality of (b) without (a)'s test-breakage cost, and is grounded in the corrected layering finding (CLI is already allowed to hold an open `openkos.graph` store — `query`/`reindex` do it today).

## #195 scope caveat

`status` today performs **three** independent `_iter_docs` walks (`survey_bundle`, `lint_check.collect_docs`, `graph_edge_summary`'s `build_graph`), not two. The issue's own proposed options ("(1) fold edge info into `survey_bundle`" or "(2) build once, pass to both") only address the `build_graph` one. Option (1) would mean the canonical `model.okf` layer starts computing graph-projection data (link extraction, `relations:` typing, proximity candidates) — that would either duplicate `graph/sqlite_graph.py`'s logic in the canonical layer or force `model` to import `graph`, which IS a live, tested violation (`test_base.py::test_canonical_layer_does_not_import_graph`). Recommend explicitly scoping #195 to "eliminate the redundant `build_graph` call inside `status`'s edge-summary line" only (via the shared-store parameter above), and NOT attempting to also fold `collect_docs` or `survey_bundle` into one true single walk — that is materially bigger, crosses more logic boundaries, and isn't what either linked spec requirement demands. `status`'s docstring claim of a single scan should be corrected/qualified rather than chased into a bigger refactor.

## Test Surface

- `tests/unit/cli/test_suggest_relations.py`, `tests/unit/cli/test_contradictions.py`, `tests/unit/cli/test_status.py` cover these CLI paths today and will need updates once the shared-store call sites change (mocking/assertions around `build_graph`/`candidate_edges`/`find_contradictions`/`graph_edge_summary` call counts, if such assertions are added as regression guards for "only one `build_graph` call per invocation").
- `tests/unit/resolution/test_edge_typing.py` and `tests/unit/resolution/test_contradiction.py` cover the resolution-layer functions directly; adding the optional `store=` keyword must not require touching any of the ~30+ existing unpacking call sites in `test_contradiction.py` (confirmed: they all call `find_contradictions(bundle_dir, llm=llm, ...)` and unpack `verdicts, total = ...`).
- `tests/unit/cli/conftest.py` **exists already** (verified) — currently holds only `_OfflineOllama`/`_offline_ollama_by_default`. No `vectors.db` fixture exists there yet.
- The three duplicated helpers are **confirmed still present and still divergent exactly as described**:
  - `tests/unit/cli/test_suggest_relations.py:74` `_touch_vectors_db` and `tests/unit/cli/test_contradictions.py:73` `_touch_vectors_db` — byte-identical body and docstring (verified line-for-line).
  - `tests/unit/cli/test_status.py:40` `_write_nonempty_vectors_db` — same SQL/logic, third name, slightly shorter docstring.
  - None of the three is a `conftest.py` fixture; each is a plain module-level helper function called directly (e.g. `_touch_vectors_db(tmp_path)`).

## Existing Specs

- `openspec/specs/graph-projection/spec.md` — "Requirement: In-Memory SQLite Node-Edge Projection" states `build_graph(bundle_dir)` is "rebuild-per-run" — this pins that a *fresh call* to `build_graph` rebuilds from scratch (no cross-invocation caching), but does **not** mandate that a single command invocation must call `build_graph` more than once. Sharing one already-open store within one CLI invocation does not violate this requirement — no scenario tests "must call build_graph twice per command."
- `openspec/specs/status/spec.md` — "Requirement: Needs-Attention Reports Concept-to-Concept Edge State" pins the three-state message vocabulary and its scenarios, not the implementation's walk count. No requirement pins "two builds."
- `openspec/specs/candidate-edge-seeding/spec.md`, `openspec/specs/contradiction-detection/spec.md` — likely need a small amendment noting the optional shared-store parameter exists (implementation detail) if the design phase wants it captured; no scenario currently blocks the change.
- `openspec/specs/derived-index-cache/spec.md` — covers the **on-disk** persisted `.openkos/graph.db` written only by `reindex`, read-only via `open_graph_store_readonly` (used today only by `query`, per `_open_graph_or_degrade` at `cli/main.py:5440`). `suggest-relations`/`contradictions`/`status` deliberately do NOT read this on-disk cache — they call `build_graph` fresh every time because they need live truth without requiring a prior `reindex` run, and using the persisted cache would introduce staleness semantics this change should not touch. **This is the main risk to flag**: any temptation during design/apply to "just read `graph.db` instead of rebuilding" would silently change staleness behavior and cross into `derived-index-cache`'s spec — out of scope for this change, which should stay strictly "reuse the in-memory build within one invocation," never "switch to the on-disk cache."

## Sequencing

Confirmed: **#197 should land before #196/#195.** The fixes for #196/#195 will modify exactly the CLI-level tests (`test_suggest_relations.py`, `test_contradictions.py`, `test_status.py`) that currently hold the three divergent `vectors.db`-seeding helpers, and will very likely need new test cases asserting single-build behavior in those same files. Landing #197's shared `conftest.py` fixture first means the #196/#195 test changes are written against one shared fixture from the start, instead of touching (or further diverging) a fourth copy.

## Risks and Unknowns

- **Layering docstrings are stale in two places** (`edge_typing.py`'s "cli/main.py MUST NOT import openkos.graph directly" and `contradiction.py`'s equivalent claim). The design/apply phases should correct these docstrings as part of the change, since they actively mislead about the current, tested constraint.
- **Return-shape stability**: any design that changes `find_contradictions`'s or `candidate_edges`'s return type (rather than adding an optional keyword) will touch ~30+ existing test call sites for reasons unrelated to what those tests assert — a real regression-risk/reviewer-burden multiplier. Prefer the additive-keyword design.
- **`derived-index-cache` boundary**: do not conflate this change with reading the persisted `.openkos/graph.db`; that changes staleness semantics and belongs to a different spec/change.
- **`status`'s 3-walk reality**: don't over-scope #195 into merging `collect_docs`/`survey_bundle` — that's a bigger change than either issue asks for or either spec requires.
- **Issue line-number drift**: every specific line reference in the original issue text (#195, #196) has drifted from the current tree (see Current State section above for corrected locations) — the design phase should not copy them forward without re-verifying at implementation time, since more commits may land between explore and apply.

## Ready for Proposal

Yes. The investigation grounds a concrete, low-risk design: an additive `store:` keyword on `graph_edge_summary`/`candidate_edges`/`find_contradictions`, CLI-side call-site restructuring in `suggest_relations_cmd`/`contradictions`/`status` to open one store per invocation, #197's conftest fixture landing first, and explicit scope guards against the `derived-index-cache` on-disk path and against over-scoping #195 into a full single-walk `status`.
