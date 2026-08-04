# Tasks: bounded-edge-seeding (issue #378)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | PR1 ~130-170; PR2 ~230-280 (~380-450 total) |
| 400-line budget risk | Low per slice |
| Chained PRs recommended | Yes |
| Suggested split | PR1 Source exclusion (seed set + both guards) -> PR2 rank + cap + truncation report |
| Delivery strategy | ask-on-risk |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Exclude `Source` docs from the pass-3 seed set (anchor list AND both endpoint guards) | PR1 | `uv run pytest tests/unit/graph/test_sqlite_graph.py -k pass_three or provenance` | N/A — pure in-process projection logic, no CLI/process boundary | `git revert` PR1; one-file change in `graph/sqlite_graph.py`, candidate rows are projection-ephemeral |
| 2 | Distance-retaining dedup, deterministic rank + cap, `CandidateReport`, CLI/curate truncation notice | PR2 | `uv run pytest tests/unit/graph/test_sqlite_graph.py tests/unit/cli/ -k candidate or truncat or notice` | `openkos suggest-relations` / `openkos contradictions` / `openkos curate` on a bundle sized over the cap | `git revert` PR2; targets PR1's branch (Feature Branch Chain per design's Migration/Rollout note) |

## PR1 — Slice 1: Source Exclusion (targets: main)

Satisfies: `openspec/changes/bounded-edge-seeding/specs/graph-projection/spec.md` MODIFIED requirement "Third Pass — Embedding-Proximity Candidate Edges", scenarios "Source document does not propose a candidate edge", "...does not receive one", "Concept-to-Concept candidates are unaffected", "Provenance-mirror derived_from edges to a Source are unaffected".

### Phase 1.1 — RED: seed-set exclusion tests

- [x] 1.1.1 `tests/unit/graph/test_sqlite_graph.py::test_pass_three_excludes_source_docs_from_the_seed_node_set` — assert `stub.received == [["concepts/a", "concepts/b"]]`, i.e. no `sources/*` id is ever offered to `candidates.pairs(...)`.
- [x] 1.1.2 `tests/unit/graph/test_sqlite_graph.py::test_pass_three_drops_a_candidate_pair_originating_from_a_source` — stub returns `("sources/call", "concepts/a")`; assert zero candidate rows.
- [x] 1.1.3 `tests/unit/graph/test_sqlite_graph.py::test_pass_three_drops_a_candidate_pair_targeting_a_source` — stub returns `("concepts/a", "sources/call")`; assert zero rows. This is the receiving-direction case the membership guards (not the anchor list) must catch — the guard `VectorProximitySource.pairs` (`proximity.py:126`) never filters its own hits.
- [x] 1.1.4 `tests/unit/graph/test_sqlite_graph.py::test_pass_three_still_seeds_concept_to_concept_pairs` — Concept<->Concept pair still inserted, unaffected by the filter.

### Phase 1.2 — GREEN: `seed_node_ids` in `_populate_graph_tables`

- [x] 1.2.1 `src/openkos/graph/sqlite_graph.py`, inside the `if candidates is not None:` block (`:386-411`) — add `seed_node_ids = {cid for cid, metadata in metadatas if metadata.get("type") != "Source"}` before the `candidate_rows` comprehension.
- [x] 1.2.2 `src/openkos/graph/sqlite_graph.py:404` — change the anchor list from `candidates.pairs(sorted(node_ids))` to `candidates.pairs(sorted(seed_node_ids))`.
- [x] 1.2.3 `src/openkos/graph/sqlite_graph.py:405-406` — change both membership guards from `pair.source_id in node_ids and pair.target_id in node_ids` to `pair.source_id in seed_node_ids and pair.target_id in seed_node_ids`. Both edits are required together — filtering only the anchor list leaves the receiving-direction case (1.1.3) uncovered, since `VectorProximitySource.pairs` queries the whole `vectors.db` and never filters its own hits against its `concept_ids` argument.
- [x] 1.2.4 Run 1.1.1-1.1.4 green. Confirm `seed_node_ids ⊆ node_ids` is preserved by construction (no new node-membership guarantee needed).
- [x] 1.2.5 Update the module docstring (`sqlite_graph.py:41-55`, the "CANDIDATE" paragraph) and `_populate_graph_tables`'s docstring (`:292-315`) to state the Source exclusion on both ends.

### Phase 1.3 — Regression tasks (must pass UNEDITED)

- [x] 1.3.1 `test_pass_three_is_a_no_op_when_no_source_is_given` (`:1467`) — unedited, green.
- [x] 1.3.2 `tests/unit/graph/test_sqlite_graph.py::test_pass_one_and_two_output_is_identical_with_and_without_a_source` (`:1481`) — unedited, green.
- [x] 1.3.3 New test `test_source_exclusion_leaves_the_derived_from_provenance_mirror_intact` — extend `_write_doc_with_provenance` fixture: concept with `provenance: [sources/foo]` + `## Related` link; assert edge `-> sources/foo` still carries `relation_type == "derived_from"`, produced by passes 1/2, unaffected by the pass-3 filter.
- [x] 1.3.4 `tests/unit/graph/test_proximity.py` (all) — unedited, green; proves `graph/proximity.py` was not touched.
- [x] 1.3.5 Existing pass-1/pass-2 tests, `test_sqlite_graph.py:105-1428` — unedited, green.
- [x] 1.3.6 `test_pass_three_row_order_is_deterministic_and_canonical` (`:1571`) — unedited, green; two-build byte-identity preserved.

### Phase 1.4 — Acceptance criterion (i) + PR1 gate

- [x] 1.4.1 Explicit assertion: no candidate edge row has an endpoint whose id starts `sources/` (covered jointly by 1.1.2/1.1.3; add one combined assertion in `test_pass_three_drops_a_candidate_pair_originating_from_a_source`/`...targeting_a_source` if not already atomic).
- [x] 1.4.2 Run `uv run pytest`; record pass/fail. Confirm branch coverage gate (`fail_under = 90`) still passes.
- [x] 1.4.3 Confirm the diff touches only `src/openkos/graph/sqlite_graph.py` and `tests/unit/graph/test_sqlite_graph.py` — no `cli/` or `resolution/` changes in PR1.
- [x] 1.4.4 `ruff check` + `ruff format` + `mypy strict` green.

## PR2 — Slice 2: Rank + Cap + Truncation Report (targets: PR1 branch, Feature Branch Chain)

Satisfies: `specs/graph-projection/spec.md` ADDED requirement "Third Pass — Bounded Candidate Output Per Run" and all five of its scenarios.

### Phase 2.1 — RED: distance-retaining dedup, rank, cap

- [x] 2.1.1 `tests/unit/graph/test_sqlite_graph.py::_StubCandidateSource` — extend to accept `(source, target, distance)` triples (currently hardcodes `distance=0.1`), keeping existing 2-tuple call sites working via a default.
- [x] 2.1.2 `test_pass_three_truncates_to_the_candidate_cap` — 60 stub pairs -> exactly `_MAX_CANDIDATE_EDGES` (50) rows.
- [x] 2.1.3 `test_pass_three_retains_the_closest_candidates_by_distance` — over-cap set with mixed distances -> retained ids are the smallest-distance ones.
- [x] 2.1.4 `test_pass_three_breaks_distance_ties_by_pair_id` — equal distances -> lexicographic `(source_id, target_id)` selection.
- [x] 2.1.5 `test_pass_three_reports_the_pre_cap_total_when_truncating` — `store.candidate_report == CandidateReport(produced=60, retained=50)`.
- [x] 2.1.6 `test_pass_three_reports_no_truncation_under_the_cap` — `produced == retained`; notice suppressed downstream.
- [x] 2.1.7 `test_dedup_against_earlier_passes_runs_before_the_cap` — a duplicate of a pass-1/pass-2 edge at the closest distance does not consume a cap slot (proves ordering: dedup BEFORE truncation, per `contradiction.py`'s post-review HIGH correction).
- [x] 2.1.8 `test_under_cap_insertion_order_is_unchanged` — under-cap rows byte-identical to the pre-slice-2 build (retained slice re-sorted by id before insert).

### Phase 2.2 — GREEN: `_MAX_CANDIDATE_EDGES`, `CandidateReport`, ranked dedup

- [x] 2.2.1 `src/openkos/graph/sqlite_graph.py` — add `_MAX_CANDIDATE_EDGES: Final[int] = 50` module constant with the docstring from design's Interfaces/Contracts section.
- [x] 2.2.2 `src/openkos/graph/sqlite_graph.py` — add `@dataclass(frozen=True) class CandidateReport: produced: int = 0; retained: int = 0`.
- [x] 2.2.3 `src/openkos/graph/sqlite_graph.py:402-409` — replace the `set[tuple[str, str]]` comprehension with a `dict[tuple[str, str], float]` keyed by the canonical `(min, max)` pair, keeping the smallest `distance` per pair (mirrors `proximity.py:143-144`'s tie rule). Order: `pairs()` -> endpoint guard (seed_node_ids) -> self-pair drop -> `seen` dedup -> best-distance collapse -> rank -> slice `[:_MAX_CANDIDATE_EDGES]` -> re-sort by id -> insert.
- [x] 2.2.4 `src/openkos/graph/sqlite_graph.py` — `ranked = sorted(best, key=lambda key: (best[key], key))`; `retained = sorted(ranked[:_MAX_CANDIDATE_EDGES])`; insert `retained` in that (id-sorted) order.
- [x] 2.2.5 `src/openkos/graph/sqlite_graph.py` — `_populate_graph_tables` returns `tuple[list[str], CandidateReport]`; update both call sites (`build_graph`, `write_graph_store`) and `SqliteGraphStore.__init__` to accept `candidate_report: CandidateReport | None = None` (defaulted so `open_graph_store_readonly` is untouched).
- [x] 2.2.6 Run 2.1.1-2.1.8 green.
- [x] 2.2.7 Update module + `_populate_graph_tables` docstrings for the ranked/capped/reported behavior.

### Phase 2.3 — RED: truncation notice at CLI call sites

- [ ] 2.3.1 `tests/unit/cli/test_suggest_relations.py` — over-cap fixture emits `"{retained} of {produced} candidate edge(s) shown (cap reached)"`; under-cap fixture emits nothing.
- [ ] 2.3.2 `tests/unit/cli/test_contradictions.py` — same two cases at `main.py:8418`.
- [ ] 2.3.3 `tests/unit/cli/` (curate test module) — over-cap fixture: `curate`'s Structure gate line carries the same notice via `StageProbe.notice`; under-cap: no notice.

### Phase 2.4 — GREEN: CLI/curate wiring

- [ ] 2.4.1 `src/openkos/cli/main.py:8088-8095` (`suggest-relations`) — render the notice from `store.candidate_report` when `produced > retained`.
- [ ] 2.4.2 `src/openkos/cli/main.py:8418` (`contradictions`) — same rendering.
- [ ] 2.4.3 `src/openkos/cli/curate.py` — add `StageProbe.notice: str | None = None`; set it from `_structure_probe`; echo it in `gate()` (`curate.py:212`) immediately before `cost_line`.
- [ ] 2.4.4 Run 2.3.1-2.3.3 green. If `curate` wiring exceeds ~20 lines beyond the design's estimate, stop and flag for a slice 3 rather than growing PR2 (per design's Open Questions).

### Phase 2.5 — Docs / CHANGELOG

- [ ] 2.5.1 `CHANGELOG.md`, under `## [Unreleased]` — add an entry describing the bounded candidate-edge output and the new truncation notice, referencing issue #378 (this repo's `[Unreleased]` section is actively used per-issue; the archived `concept-edge-seeding` change did not touch `docs/`, only `CHANGELOG.md`-equivalent release notes at the time — confirm current convention still routes through `CHANGELOG.md`).
- [ ] 2.5.2 `docs/cli.md` (curate section, `:381`, the cost-gate paragraph) — add one sentence noting the Structure gate can carry a truncation notice when candidate edges exceed the cap.

### Phase 2.6 — Regression tasks (must pass UNEDITED)

- [x] 2.6.1 `tests/unit/graph/test_proximity.py` (all) — unedited, green.
- [x] 2.6.2 Slice 1's five tests (Phase 1.1 + 1.3.3) — unedited, green.
- [x] 2.6.3 Existing pass-1/pass-2 tests (`test_sqlite_graph.py:105-1428`) — unedited, green.
- [x] 2.6.4 Two-build determinism: `test_pass_three_row_order_is_deterministic_and_canonical` plus a new `test_pass_three_ranking_and_truncation_is_deterministic_across_two_builds` (mirrors design's Deterministic-ranking scenario) — over-cap fixture, two `build_graph` calls, identical retained set/order/notice.

### Phase 2.7 — Acceptance criterion (ii) + PR2 gate

- [x] 2.7.1 Explicit assertion: candidate output never exceeds `_MAX_CANDIDATE_EDGES` at any bundle size (2.1.2), and when truncation occurs the report carries the correct pre-cap total (2.1.5).
- [x] 2.7.2 Run `uv run pytest`; record pass/fail. Confirm branch coverage gate (`fail_under = 90`) still passes.
- [x] 2.7.3 Delivery correction: PR1 merged to `main` first, so slice 2 branches from and targets `main` (stacked-to-main), not PR1's branch. Slice 2 measured 740 insertions against the 400-line PR budget, so it ships as two PRs: **2a** (graph — cap, ranking, `CandidateReport`, phases 2.1/2.2/2.6) and **2b** (CLI — truncation notice, docs, phases 2.3/2.4/2.5). Each targets `main` in sequence.
- [x] 2.7.4 `ruff check` + `ruff format` + `mypy strict` green.
- [ ] 2.7.5 Verify proposal success criteria: reported bundle's candidate count ~74 -> ~25 (slice 1) confirmed unaffected by slice 2's cap (25 < 50, no-op); `status`'s count and `suggest-relations`'s candidate count agree.
