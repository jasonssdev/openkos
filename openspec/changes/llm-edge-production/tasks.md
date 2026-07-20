# Tasks: LLM Edge Production (MVP-2 Slice 2b)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~650-750 total (PR1 ~330-380, PR2 ~320-370) |
| 400-line budget risk | Medium (per-PR; each unit near/under 400 — single PR would be High) |
| Chained PRs recommended | Yes |
| Suggested split | PR1 (library) → PR2 (CLI wiring) |
| Delivery strategy | ask-on-risk |
| Chain strategy | feature-branch-chain (cached from slice 2a; pending user confirmation) |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: Medium

Note: design v2's rollout section calls this "under 400-line budget — single PR." Precedent check against slice 1
(`sdd/typed-relationships/tasks`, an adjudication+CLI slice of comparable shape) shows that shape was itself chained
into 4 PRs (~530 total); this slice is a subset (one module, one verb) but combined prod+test size still lands near
or over the 400-line single-PR budget once fail-closed parse tests and 3-tier Ollama CLI tests are counted. Flagging
High/Medium risk rather than accepting the design's single-PR assumption at face value.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `resolution/edge_typing.py`: `EdgeSuggestion`, `untyped_edges`, `suggest_edge_types` (LLM leaf), `suggest_relations` (orchestrator) | PR1 (base: tracker/feature branch) | `uv run pytest tests/unit/resolution/test_edge_typing.py` | N/A — pure library over `GraphStore`/`LLMBackend` Protocols, no CLI/live-Ollama harness needed | Revert PR1; module unused/unimported until PR2 wires it, zero CLI surface change |
| 2 | CLI verb `suggest-relations` + docstring note + guard regression | PR2 (base: PR1 branch) | `uv run pytest tests/unit/cli/test_suggest_relations.py tests/unit/graph/test_analysis.py tests/unit/graph/test_base.py` | `uv run openkos suggest-relations` against a fixture bundle with untyped body-link edges (mocked/real Ollama) | Revert PR2 only; PR1's `edge_typing.py` stays inert (unimported by CLI) until re-applied |

## Phase 1: Foundation — `EdgeSuggestion` + `untyped_edges`

- [x] 1.1 RED: `tests/unit/resolution/test_edge_typing.py` (new) — `untyped_edges(store)` returns only edges with `relation_type is None`, deterministic order (spec: "Already-typed edges are excluded from suggestions").
- [x] 1.2 GREEN: `src/openkos/resolution/edge_typing.py` (new) — `EdgeSuggestion` frozen dataclass (`edge`, `suggested_type: str | None`, `rationale`); `untyped_edges(store: GraphStore) -> list[Edge]`.

## Phase 2: LLM Leaf — `suggest_edge_types` (fail-closed)

- [x] 2.1 RED: `test_edge_typing.py` — one `EdgeSuggestion` per input edge, same order (spec: "Verb lists every untyped edge with a valid suggestion").
- [x] 2.2 RED: `test_edge_typing.py` — malformed/unparseable LLM reply for one edge degrades only that edge (`suggested_type=None`), others unaffected, no crash (spec: "Malformed LLM output degrades one item, not the run").
- [x] 2.3 RED: `test_edge_typing.py` — a suggested type failing `validate_relation_type` degrades to `suggested_type=None`, never surfaced as valid (spec: "Invalid suggested type is not surfaced as valid").
- [x] 2.4 RED: `test_edge_typing.py` — an `OllamaError`-family exception from `llm.chat` propagates unswallowed (mirrors `adjudication.py`'s contract).
- [x] 2.5 GREEN: `edge_typing.py` — `_load_doc(bundle_dir, concept_id)` guarded single-doc re-read; `_build_messages(edge, src_doc, tgt_doc)` 2-message JSON-only prompt.
- [x] 2.6 GREEN: `edge_typing.py` — 3-step fail-closed JSON extraction + `validate_relation_type` gate (mirrors `adjudication.py` pattern); `suggest_edge_types(edges, *, bundle_dir, llm) -> list[EdgeSuggestion]`.
- [x] 2.7 REFACTOR: dedupe parse-helper style against `resolution/adjudication.py` — module-local copies only, no cross-import of its private `_`-prefixed symbols (design D4).

## Phase 3: Orchestrator — `suggest_relations` Owns `build_graph`

- [x] 3.1 RED: `test_edge_typing.py` — `suggest_relations(bundle_dir, *, llm)` opens `build_graph` internally, filters untyped, delegates to `suggest_edge_types` (spec: "Verb lists every untyped edge...", "Already-typed edges are excluded").
- [x] 3.2 GREEN: `edge_typing.py` — `suggest_relations(bundle_dir: Path, *, llm: LLMBackend) -> list[EdgeSuggestion]`.
- [x] 3.3 VERIFY: `tests/unit/graph/test_base.py::test_canonical_layer_does_not_import_graph` — run as regression, unmodified, stays green (spec: "Canonical layer has no graph import").

## Phase 4: CLI Wiring — `suggest-relations` Verb

- [x] 4.1 RED: `tests/unit/cli/test_suggest_relations.py` (new) — verb prints `(source, suggested_type, target, rationale)` per untyped edge plus closing hint `openkos relate <source> <type> <target>` (spec: "Verb lists every untyped edge with a valid suggestion").
- [x] 4.2 RED: `test_suggest_relations.py` — verb performs zero writes: `CliRunner` over a fixture bundle, assert no bundle file/`index.md`/`log.md` content changes (spec: "Verb performs zero writes").
- [x] 4.3 RED: `test_suggest_relations.py` — a degraded item renders as `[?]` + "no valid type suggested", never as a valid suggestion (spec: "Invalid suggested type is not surfaced as valid").
- [x] 4.4 RED: `test_suggest_relations.py` — 3-tier `OllamaError` handling (`OllamaUnavailable`, `OllamaModelNotFound`, generic `OllamaError`), each with actionable stderr + exit 1, mirrors `adjudicate`'s ordering.
- [x] 4.5 GREEN: `src/openkos/cli/main.py` — `from openkos.resolution.edge_typing import suggest_relations` (NEVER `openkos.graph`); new `@app.command("suggest-relations")` verb (Python function `suggest_relations_cmd`, so the CLI command's own name never shadows the imported library function — tests patch `openkos.cli.main.suggest_relations` directly, mirroring how `test_adjudicate.py` patches `adjudicate_candidates`): workspace gate, build `OllamaClient`, call `suggest_relations`, render, 3-tier error handling, zero writes. Deviation: `EdgeSuggestion` is NOT imported into `cli/main.py` — the command only duck-types `result.edge`/`.suggested_type`/`.rationale`, no annotation needs the class, and importing it unused would fail the `ruff` F401 gate.
- [x] 4.6 VERIFY: `tests/unit/graph/test_analysis.py::test_cli_main_never_imports_graph_and_registers_no_graph_command` — run UNMODIFIED, stays green (spec: "Layering Invariant").

## Phase 5: Docs + Regression Sweep

- [x] 5.1 DOCS: `src/openkos/resolution/__init__.py` — update docstring: "does not import `openkos.graph` this slice" → note `edge_typing` now reads `graph` internally (derived→derived, allowed).
- [x] 5.2 VERIFY: `uv run pytest tests/unit/resolution/test_edge_typing.py tests/unit/cli/test_suggest_relations.py tests/unit/graph/test_analysis.py tests/unit/graph/test_base.py` green; `uv run ruff check . && uv run ruff format --check .` + `uv run mypy .` clean on touched files.
- [x] 5.3 VERIFY: `uv run pytest` full suite green; `uv run pytest --cov` >= 90% branch; confirm zero diff in `Relation` schema / merge-ledger / reversibility code (`model/okf.py` merge path, `bundle/relations.py`).

## Unplanned Fix (discovered during apply, not in original task list)

- [x] 5.4 FIX: `tests/unit/resolution/test_layering.py::test_resolution_package_does_not_import_graph` encoded the stale slice-1 assumption "resolution does not import graph" — the exact claim task 5.1 directs to retire. Renamed/inverted to `test_resolution_may_import_graph` (positive assertion, mirrors the existing `test_resolution_may_import_llm` pattern) so it now proves `openkos.graph` IS imported somewhere under `resolution` (by `edge_typing.py`), instead of asserting the opposite. This is NOT one of the two explicitly protected hard-invariant tests (`test_analysis.py::test_cli_main_never_imports_graph_and_registers_no_graph_command`, `test_base.py::test_canonical_layer_does_not_import_graph`) — both of those stayed green and UNMODIFIED, as required.
