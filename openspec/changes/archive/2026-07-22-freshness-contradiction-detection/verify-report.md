# Verification Report: Contradiction Detection (S3 of freshness-lint-v1)

**Change**: freshness-contradiction-detection
**Mode**: Strict TDD, full spec-driven verification (proposal/spec/design/tasks all present)
**Verdict**: **PASS**

## Completeness

- Tasks: 24/24 checked in `openspec/changes/freshness-contradiction-detection/tasks.md` (confirmed on disk: 24 `[x]`, 0 `[ ]`).
- No task incomplete; no CRITICAL/WARNING from task completeness.

## Gate Re-Run (independent, exact results)

| Command | Exit code | Result |
|---|---|---|
| `uv run pytest -q` | 0 | `1458 passed in 3.40s` |
| `uv run pytest -q tests/unit/resolution/test_contradiction.py tests/unit/cli/test_contradictions.py` | 0 | `71 passed` (52 engine leaf + 19 CLI) |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run ruff format --check .` | 0 | `108 files already formatted` |
| `uv run mypy .` | 0 | `Success: no issues found in 108 source files` |

All figures match the apply-progress artifact's self-reported numbers; independently re-run, not merely trusted.

## Spec Compliance Matrix (10 requirements / 10 scenarios)

| # | Requirement | Scenario | Covering test(s) | Status |
|---|---|---|---|---|
| 1 | Candidate Generation From Typed Graph Edges, Deduped | Symmetric and multi-edge pairs judged once | `test_candidate_pairs_symmetric_edge_pair_collapses_to_one`, `test_candidate_pairs_multi_edge_same_pair_collapses_to_one`, `test_find_contradictions_symmetric_edges_judged_exactly_once` | PASS |
| 2 | Per-Pair Verdict Shape With Cited Claims | CONTRADICTS with cited claims | `test_contradiction_verdict_carries_all_expected_fields`, `test_parse_reply_valid_contradicts_with_claims_keeps_verdict` | PASS |
| 3 | Citation-Gated Precision | Uncited CONTRADICTS degrades | `test_parse_reply_contradicts_with_empty_claims_degrades_to_uncertain`, `test_parse_reply_contradicts_with_missing_claims_key_degrades_to_uncertain` | PASS |
| 4 | Fail-Closed Reply Parsing And Confidence Coercion | Malformed reply degrades one pair only | `test_parse_reply_non_json_reply_degrades_to_uncertain_without_raising`, `test_parse_reply_non_object_json_reply_degrades_to_uncertain`, `test_parse_reply_unknown_verdict_string_degrades_to_uncertain`, `test_coerce_confidence_table[...]` (12 cases incl. NaN/Inf/bool), `test_find_contradictions_malformed_reply_degrades_only_that_pair` | PASS |
| 5 | Pair Cap With Explicit Truncation Notice | Cap truncation is reported | `test_candidate_pairs_over_cap_truncates_to_stable_sorted_prefix`, `test_find_contradictions_calls_llm_once_per_capped_candidate_pair`, `test_contradictions_cap_reached_line_present`, `test_contradictions_no_cap_reached_line_when_under_cap` | PASS |
| 6 | Read-Only `contradictions` CLI Verb, High-Confidence Default | Default view hides CONSISTENT/UNCERTAIN, zero writes | `test_contradictions_default_view_shows_only_high_confidence_contradicts`, `test_contradictions_never_writes_to_the_workspace`, `test_contradictions_never_writes_across_all_verdict_mix_scenarios` | PASS |
| 7 | `--all` Reveals Every Verdict | `--all` shows CONSISTENT and UNCERTAIN too | `test_contradictions_all_flag_shows_every_verdict`, `test_contradictions_all_flag_does_not_affect_find_contradictions_call` | PASS |
| 8 | Degrade-On-No-Model Mirrors `adjudicate`'s 3-Tier Catch | Each tier degrades cleanly with zero writes | `test_contradictions_ollama_unavailable_maps_to_exit_one`, `test_contradictions_model_not_found_maps_to_exit_one`, `test_contradictions_generic_ollama_error_maps_to_exit_one`, `test_contradictions_handler_order_specific_before_generic` | PASS |
| 9 | Empty Graph Yields Clear Message, No Crash | No typed edges | `test_find_contradictions_no_typed_edges_returns_empty_zero_llm_calls`, `test_contradictions_fresh_bundle_reports_no_candidate_pairs`, `test_contradictions_no_candidate_pairs_never_calls_llm` | PASS |
| 10 | Deterministic Candidate Pair Ordering | Repeated runs yield the same pair order | `test_candidate_pairs_deterministic_sorted_order` | PASS |

**10/10 requirements covered, 10/10 scenarios covered by real, independently re-run passing tests.** Additional CLI tests beyond the 10 core scenarios (e.g. `test_contradictions_no_auto_flag_offered`, `test_contradictions_builds_ollama_client_from_configured_model`, `test_contradictions_over_good_life_demo_is_read_only`) further corroborate read-only/config-wiring behavior without contradicting the spec.

## Determinism / No Real Ollama

Engine-leaf tests (`test_contradiction.py`) use a fake `LLMBackend` exclusively — confirmed via source read (no `OllamaClient`, `requests`, `httpx`, or network host strings in that file). CLI tests import `OllamaClient` only to (a) assert exception types raised through monkeypatched `find_contradictions`, and (b) assert the CLI constructs an `OllamaClient` from `openkos.yaml`'s configured model without invoking `.chat()` (verified via a zero-pair early return, so no network call occurs). The one integration test against the real `examples/good-life-demo` bundle (`test_contradictions_over_good_life_demo_is_read_only`) explicitly tolerates a live-or-absent Ollama and asserts only the zero-writes invariant — it does not assert on judgment content, so it cannot introduce nondeterminism into the suite (confirmed: full 1458-test suite completes in 3.40s, consistent with no network I/O attempted). Pair selection/order is proven deterministic by `test_candidate_pairs_deterministic_sorted_order` and the `_pair_key`/`sorted()` implementation in `contradiction.py`.

## Scope Confirmation

`git diff main...HEAD --stat` shows exactly 8 changed files, all additions, 2151 lines total:

- `openspec/changes/freshness-contradiction-detection/{design.md, proposal.md, tasks.md}` — SDD planning artifacts (expected)
- `openspec/changes/freshness-contradiction-detection/specs/contradiction-detection/spec.md` — delta spec inside the **change** folder (expected; this is NOT the canonical spec)
- `src/openkos/resolution/contradiction.py` — new engine leaf (365 lines)
- `src/openkos/cli/main.py` — modified, `contradictions` verb + import only (+127 lines)
- `tests/unit/resolution/test_contradiction.py` — new (645 lines)
- `tests/unit/cli/test_contradictions.py` — new (615 lines)

Confirmed:
- **No prior-slice file touched** (no diff to `edge_typing.py`, `volatility_typing.py`, `adjudication.py`, `sqlite_graph.py`, etc.).
- **No canonical `openspec/specs/contradiction-detection/` folder exists yet** (`fd contradiction openspec/specs` returns nothing; `openspec/specs/` currently lists 21 other canonical spec dirs, contradiction-detection is not among them) — correctly deferred to the archive phase.
- **No ADR file touched** — `git diff main...HEAD --stat` shows no `openspec/adr/` or similar path.

## Deviations From Design/Tasks (all reviewed, none violate spec)

1. **`find_contradictions` returns `tuple[list[ContradictionVerdict], int]`** instead of design's plain `list[ContradictionVerdict]`. Justified: tasks.md Phase 4.4 explicitly required the cap-reached signal be returned; the tuple shape satisfies both the design's list-centric return and the explicit task requirement without a new public dataclass. Spec Requirement 5 ("report MUST state truncation explicitly") is satisfied via `total_pairs > len(verdicts)` in the CLI. **Not a spec violation.**
2. **Public `is_high_confidence_contradiction()` helper** (not in design/tasks verbatim). Keeps `_CONFIDENCE_DISPLAY_THRESHOLD` private while giving the CLI a stable, non-underscore entry point — consistent with the project's existing no-cross-import-of-private-symbols convention (design D4). **Not a spec violation** — spec only requires the default-view threshold behavior, which this helper implements correctly (verified via `test_is_high_confidence_contradiction_public_helper` and the default-view CLI test).
3. **`_pair_relation_types()` helper** (not in design/tasks). Additive-only; used solely to enrich the LLM prompt with the `relation_type` linking a pair, per the design's own "LLM Prompt Contract" section ("the relation_type linking them"). `_candidate_pairs` itself is unchanged from spec (pairs + total_count only). **Not a spec violation** — required by the design's prompt contract, not a new behavior.
4. **Dangling-edge test replaced by two `_load_doc` unit tests**. Original plan (Phase 4) implied a dangling-edge-degrades-at-orchestration-level test; the developer found (and I independently confirmed by reading `sqlite_graph.py`'s `build_graph`/edge-resolution docstring) that `build_graph` silently drops edges whose endpoint doesn't resolve to a known node, so such an edge never reaches `_candidate_pairs`/`find_contradictions` at all — an orchestration-level test for that case would be untestable/vacuous. The substituted `test_load_doc_handles_unreadable_or_missing_document` and `test_load_doc_handles_unparseable_frontmatter_document` correctly test the same fail-closed-degrade guarantee at the unit level where it is actually reachable. **Not a spec violation** — no spec requirement mandates the specific test location, only the degrade behavior, which remains tested.

All four deviations are implementation/task-level refinements, not drift from the 10 spec requirements above; every spec scenario remains covered by a passing test as tabulated.

## Issues

- **CRITICAL**: None.
- **WARNING**: None.
- **SUGGESTION**: None.

## Verdict

**PASS** — 24/24 tasks complete, 10/10 spec requirements and scenarios covered by independently re-run passing tests (71/71 new, 1458/1458 full suite), all four static-analysis gates clean (ruff check, ruff format --check, mypy), scope strictly bounded to the two new files + the two new test files + the expected `contradictions` verb addition in `cli/main.py`, no prior-slice regression, no premature canonical spec/ADR write, and all four implementation deviations reviewed and confirmed spec-compliant rather than drift.

Ready for `sdd-archive` (post-apply bounded review runs first per the orchestrator's review lifecycle; archive should follow once that review's receipt is bound).
