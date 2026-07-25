# Tasks: Type Provenance-Mirror Edges As derived_from At Projection Time (#135)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~180-200 (24 src + 120-140 test + docstrings) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Provenance-mirror synthesis + downstream guard, tested | PR 1 | `uv run pytest tests/unit/graph/test_sqlite_graph.py tests/unit/resolution/test_edge_typing.py tests/unit/resolution/test_contradiction.py tests/unit/cli/test_suggest_relations.py` | `uv run openkos suggest-relations <bundle>` on a provenance-only fixture bundle | Revert `sqlite_graph.py` + `contradiction.py` hunks; graph.db rebuilds `None` typing on next reindex, no migration |

## Phase 1: RED — Graph Projection Tests (`tests/unit/graph/test_sqlite_graph.py`)

- [x] 1.1 RED: provenance-mirror body link (`provenance: [sources/foo]`, link to `sources/foo`) → edge `relation_type == "derived_from"`
- [x] 1.2 RED: multi-entry provenance (`provenance: [sources/a, sources/b]`, links to both) → both edges `derived_from`
- [x] 1.3 RED: `query --save` concept→concept provenance link (`provenance: [concepts/bar]`, link to `concepts/bar`) → `derived_from`, typed by list membership not `sources/`-prefix
- [x] 1.4 RED: genuine concept→concept link NOT in `provenance:` list → `relation_type` stays `None`
- [x] 1.5 RED: existing `relations:`-typed edge unaffected by provenance synthesis (non-regression)
- [x] 1.6 RED: dirty/dangling provenance (non-list, non-string entries) degrades to empty set, no crash

## Phase 2: GREEN — Graph Projection Implementation (`src/openkos/graph/sqlite_graph.py`)

- [x] 2.1 In `_populate_graph_tables`, build `provenance_by_source: dict[str, set[str]]` from existing per-document metadata (decoded `provenance:` frontmatter), coercing non-list/non-string entries to empty/dropped per fail-safe posture
- [x] 2.2 In the untyped body-link pass, when no `relations:` match exists, set `relation_type = "derived_from"` if `target_id in provenance_by_source[source_id]`, else keep `None` — replace, not add, the row
- [x] 2.3 Update docstring to document provenance-mirror synthesis as a second, projection-only typing source
- [x] 2.4 Run `uv run pytest tests/unit/graph/test_sqlite_graph.py` — confirm GREEN

## Phase 3: RED — Suggest-Relations Exclusion Tests

- [x] 3.1 RED (`tests/unit/resolution/test_edge_typing.py`): provenance-mirror edge absent from `_candidate_edges`/`untyped_edges` output
- [x] 3.2 RED (`tests/unit/resolution/test_edge_typing.py`): provenance-only bundle → zero candidates, assert LLM `chat` never called
- [x] 3.3 RED (`tests/unit/cli/test_suggest_relations.py`): provenance-only bundle → CLI reports zero candidates, zero LLM calls, honest "nothing to type" message
- [x] 3.4 RED (`tests/unit/cli/test_suggest_relations.py`): bundle with one provenance-mirror edge + one genuine untyped concept-to-concept edge → only the genuine edge is printed as a candidate

## Phase 4: GREEN — Confirm Suggest-Relations (no code change expected)

- [x] 4.1 Run `uv run pytest tests/unit/resolution/test_edge_typing.py tests/unit/cli/test_suggest_relations.py` — confirm GREEN with zero edits to `src/openkos/resolution/edge_typing.py` (exclusion is automatic once `relation_type` is non-`None`)

## Phase 5: RED — Contradiction Guard Tests (`tests/unit/resolution/test_contradiction.py`)

- [x] 5.1 RED: provenance-only bundle → `_candidate_pairs` yields zero candidates, `find_contradictions` makes zero LLM calls
- [x] 5.2 RED: `derived_from` edge from hand-authored `relations:` frontmatter is also excluded (guard is type-based, not origin-based)
- [x] 5.3 RED: genuine typed non-`derived_from` edge (e.g. `related_to` between two event concepts) still surfaced and judged

## Phase 6: GREEN — Contradiction Guard Implementation (`src/openkos/resolution/contradiction.py`)

- [x] 6.1 In `_candidate_pairs` (~lines 149-185), exclude edges where `relation_type == "derived_from"` from candidate pair generation
- [x] 6.2 Update docstring to note the `derived_from` exclusion and rationale (derivation, not contradiction candidate)
- [x] 6.3 Run `uv run pytest tests/unit/resolution/test_contradiction.py` — confirm GREEN

## Phase 7: Non-Regression

- [x] 7.1 Confirm existing typed `relate`-created edge tests in `test_sqlite_graph.py` / `test_contradiction.py` still pass unchanged
- [x] 7.2 Add/confirm a PPR/retrieval non-regression check (`graph_retrieve.py` / `analysis.py` undirected view) — edge set and PPR results unaffected by the type-attribute flip

## Phase 8: Quality Gate

- [x] 8.1 `uv run pytest` — full suite green
- [x] 8.2 `uv run ruff check . && uv run ruff format --check .` — clean
- [x] 8.3 `uv run mypy .` — clean
