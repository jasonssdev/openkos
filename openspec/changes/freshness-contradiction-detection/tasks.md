# Tasks: Contradiction Detection (S3 of freshness-lint-v1)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1150–1300 (leaf ~260 + leaf tests ~550 + CLI verb ~130 + CLI tests ~350) |
| 400-line budget risk | High |
| Chained PRs recommended | No (primary recommendation) |
| Suggested split | Single PR, `size:exception` (verb+engine tightly coupled); optional fallback split below |
| Delivery strategy | auto-forecast |
| Chain strategy | size-exception |

Decision needed before apply: Yes
Chained PRs recommended: No
Chain strategy: size-exception
400-line budget risk: High

Rationale: mirrors S2b (`edge_typing.py` 269 + tests 512 + CLI verb ~100 + CLI
tests 361 ≈ 1240 total) — same engine-leaf + thin-verb shape, one PR. Verb and
engine share the same graph/LLM contract; splitting adds cross-PR coupling
risk without reducing reviewer cognitive load much. If the maintainer prefers
a split instead of `size:exception`, use the fallback below.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 (primary) | Full slice: engine leaf + CLI verb + tests | PR 1 (size:exception) | `uv run pytest tests/unit/resolution/test_contradiction.py tests/unit/cli/test_contradictions.py` | `openkos contradictions` over a fixture workspace with a real typed edge | Revert `contradiction.py`, `contradictions` verb block in `cli/main.py`, both test files |
| A (fallback) | Engine leaf only: `contradiction.py` + tests | PR A | `uv run pytest tests/unit/resolution/test_contradiction.py` | N/A — library only, no CLI surface yet | Revert `contradiction.py` + its test file |
| B (fallback, depends on A) | CLI verb `contradictions` + tests | PR B | `uv run pytest tests/unit/cli/test_contradictions.py` | `openkos contradictions` over fixture workspace | Revert verb block + its test file |

## Phase 1: Engine Leaf Foundation
- [x] 1.1 RED `tests/unit/resolution/test_contradiction.py`: `Verdict` enum values; `ContradictionVerdict` shape (sorted `pair_ids`, verdict, confidence, rationale, `conflicting_claims`).
- [x] 1.2 GREEN `src/openkos/resolution/contradiction.py`: add `Verdict` enum, `ContradictionVerdict` dataclass, `MAX_PAIRS=200`, `CONFIDENCE_DISPLAY_THRESHOLD=0.7`.

## Phase 2: Candidate Pairs (Req: Dedup, Ordering, Cap)
- [x] 2.1 RED: symmetric `A→B`/`B→A` and multi-edge pairs collapse to one via `frozenset({source_id,target_id})`.
- [x] 2.2 RED: deterministic `sorted(pair)` order across repeated runs.
- [x] 2.3 RED: >200 deduped pairs truncates to a stable sorted prefix + cap-reached signal with total count.
- [x] 2.4 GREEN: `_candidate_pairs(store)` — typed edges only, frozenset dedup, sorted, `[:MAX_PAIRS]`, returns `(pairs, total_count)`.

## Phase 3: Fail-Closed Parse (Req: Verdict Shape, Citation Gate, Parse)
- [x] 3.1 RED table tests (fake `LLMBackend`): valid `CONTRADICTS` w/ claims; `CONTRADICTS` w/ empty/missing claims → `UNCERTAIN`; unknown verdict string → `UNCERTAIN`; non-JSON/non-object reply → `UNCERTAIN` without raise; confidence `NaN`/`Inf`/bool → `0.0`; confidence clamp `[0,1]`.
- [x] 3.2 GREEN: module-local clone of `_extract_json_object`/`_map_verdict`/`_coerce_confidence` (no cross-import, D4) + citation-gate coercion.

## Phase 4: Orchestration (Req: propagation, empty graph)
- [x] 4.1 RED: one pair's malformed reply degrades only that pair; the other pair's result is unaffected, neither raises.
- [x] 4.2 RED: `OllamaError` raised by `llm.chat` propagates unswallowed.
- [x] 4.3 RED: no typed edges → `[]`, zero `llm.chat` calls.
- [x] 4.4 GREEN: `find_contradictions(bundle_dir, *, llm)` — `build_graph`, `_candidate_pairs`, guarded `_load_doc` clone (x2/pair), one `llm.chat`/pair, returns verdict list + cap-reached signal.

## Phase 5: CLI Verb `contradictions` (`cli/main.py`)
- [x] 5.1 RED `tests/unit/cli/test_contradictions.py`: `require_workspace` refusal message; `read_config` `(OSError, ValueError)` guard message.
- [x] 5.2 RED: 3-tier ordered handler (`OllamaUnavailable` → `OllamaModelNotFound` → generic `OllamaError`), each own message, exit 1, zero writes.
- [x] 5.3 RED: default view shows only `CONTRADICTS` with confidence ≥ 0.7; hides `CONSISTENT`/`UNCERTAIN` and low-confidence `CONTRADICTS`.
- [x] 5.4 RED: `--all` shows every verdict; `find_contradictions` call itself is unaffected by the flag.
- [x] 5.5 RED: no candidate pairs → "No candidate pairs found." exit 0, no `llm.chat` call.
- [x] 5.6 RED: cap-reached → "N of M pairs shown (cap reached)" line present.
- [x] 5.7 RED: zero bundle writes across all scenarios above.
- [x] 5.8 GREEN: add `contradictions` command — `require_workspace`, `read_config` guard, `OllamaClient(model=cfg.model)`, 3-tier handler wrapping `find_contradictions`, `--all` option, threshold filter, cap-reached line, empty-graph message. CLI imports only `contradiction`, never `openkos.graph` (D2/D6).

## Phase 6: Verification Gate
- [x] 6.1 `uv run pytest` — full suite clean.
- [x] 6.2 `uv run ruff format .` then `uv run ruff format --check .`.
- [x] 6.3 `uv run ruff check .` clean.
- [x] 6.4 `uv run mypy .` clean.
