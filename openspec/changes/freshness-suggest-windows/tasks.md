# Tasks: freshness-suggest-windows (S2) — `suggest-volatility`

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1,300 (config/lint layer ~320, engine leaf ~700, CLI verb ~350) |
| 800-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (config+precedence) ∥ PR 2 (engine leaf) → PR 3 (CLI verb) |
| Delivery strategy | auto-forecast (no `ask-on-risk`/`single-pr` gate requested — proceeds with PR 1 first slice) |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
800-line budget risk: High

Note: S1 lesson applied — `test_edge_typing.py` (24 tests) and `test_suggest_relations.py` (14 tests) are the closest real mirrors; the engine-leaf test file alone is forecast at ~450 lines, which is why PR 2 is isolated rather than folded into PR 3.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `type_tiers` config passthrough + `window_for_doc` precedence layer | PR 1 | `uv run pytest tests/unit/test_config.py tests/unit/test_lint.py -k type_tiers or window_for_doc` | N/A — pure config/precedence unit, no external process | Revert `config.py`, `lint.py`, template block, canonical `concept-volatility/spec.md` delta; S1 precedence collapses byte-identical |
| 2 | `resolution/volatility_typing.py` engine leaf (config-free, LLM-injected) | PR 2 | `uv run pytest tests/unit/resolution/test_volatility_typing.py` | N/A — leaf takes a fake `LLMBackend`, no real Ollama call in tests | Revert `resolution/volatility_typing.py` + its test file; no other module imports it yet |
| 3 | `suggest-volatility` CLI verb wiring | PR 3 | `uv run pytest tests/unit/cli/test_suggest_volatility.py` | Manual: `openkos suggest-volatility` inside a real workspace with `ollama serve` running, `bge-m3`/configured model pulled | Revert the `@app.command("suggest-volatility")` block in `cli/main.py` + its test file; PR 1/2 remain valid standalone |

PR 1 and PR 2 touch disjoint files (config/lint vs. new engine module) and can be developed in parallel; PR 3 depends on PR 2's `suggest_volatility` signature and should stack after it.

## Phase 1: Config Layer — `type_tiers` passthrough (PR 1)

- [x] 1.1 RED: add `test_read_config_type_tiers_defaults_to_empty_map_when_absent`, `..._falls_back_to_empty_map_on_explicit_null`, `..._passes_through_verbatim` to `tests/unit/test_config.py` (mirror `volatility_windows` tests at test_config.py:551-590).
- [x] 1.2 GREEN: add `Config.type_tiers: dict[str, str]` field (config.py:339 mirror) and `read_config` parsing (`raw.get("type_tiers")`, `is not None else {}`, config.py:379/396 mirror).
- [x] 1.3 Add commented `type_tiers:` block to `src/openkos/templates/openkos.yaml.template` (mirror commented `volatility_windows` block at lines 5-7).

## Phase 2: Precedence Layer — `window_for_doc` type_tiers step (PR 1)

- [x] 2.1 RED: add lint precedence tests to `tests/unit/test_lint.py` covering the 4 `concept-volatility` spec scenarios: (a) `type_tiers` override wins over registry default, (b) invalid tier / unknown type in `type_tiers` falls through without raising, (c) absent `type_tiers` reproduces byte-identical S1 precedence (regression pin), (d) `type_tiers` resolving to `static` is never flagged stale.
- [x] 2.2 RED: add `resolve_windows` non-mapping guard test — `cfg.type_tiers` as a list/scalar degrades to `{}` (mirror `volatility_windows` non-mapping guard, lint.py:212-213).
- [x] 2.3 GREEN: add `type_tiers: dict[str, str]` field to `VolatilityWindows` (lint.py:~180) and populate it in `resolve_windows` with the `isinstance(dict)` guard (lint.py:212-213 mirror).
- [x] 2.4 GREEN: insert the `type_tiers` override step into `window_for_doc` (lint.py:231-254) between the per-concept `volatility` check and the registry-default fallback, per design's precedence snippet — every degrade path (non-mapping, unknown type, invalid tier) falls through via `not in VOLATILITY_TIERS` guards, never raises. **Deviation**: also gated the `type_tiers` lookup on `doc.type in types.TYPE_TO_DEFAULT_VOLATILITY` (registry membership), not just tier-value validity — the design's literal snippet only checked tier validity, which would incorrectly HONOR a `type_tiers` entry keyed by an unregistered type name (e.g. `type_tiers: {UnknownType: slow}` matching a doc whose `type` is literally `"UnknownType"`), contradicting the ADDED requirement's "type name is unknown ... MUST be ignored" clause. Added test `test_window_for_doc_type_tiers_unknown_type_key_is_ignored` pins this.
- [x] 2.5 REFACTOR: confirm `window_for_doc`/`resolve_windows` docstrings describe the new 4-step precedence (per-concept → `type_tiers` → registry default → global fallback).

## Phase 3: Spec Sync (PR 1)

- [x] 3.1 Apply the `type_tiers` ADDED requirement + precedence MODIFIED requirement from `openspec/changes/freshness-suggest-windows/specs/concept-volatility/spec.md` into the canonical `openspec/specs/concept-volatility/spec.md`, in lockstep with Phase 2 (note for archive: pre-merged like S1's lint spec).

## Phase 4: Engine Leaf — `resolution/volatility_typing.py` (PR 2)

- [x] 4.1 RED: create `tests/unit/resolution/test_volatility_typing.py` (mirror `test_edge_typing.py` structure) covering: one `TierSuggestion` per distinct type in sorted-name order; fail-closed JSON parse (missing/malformed/invalid-tier → `suggested_tier=None`, `rationale` never blank); `OllamaError` propagates unswallowed from `llm.chat`; deterministic sampling — same bundle yields identical set+order of sampled bodies across two calls (N=5 concepts by sorted `identity`, truncated to M=1000 chars) per spec's "Deterministic Input Selection" scenario.
- [x] 4.2 GREEN: create `src/openkos/resolution/volatility_typing.py` — config-free leaf, `TierSuggestion` frozen dataclass (`type_name`, `current_default`, `suggested_tier: str | None`, `rationale`), `suggest_volatility(bundle_dir: Path, *, llm: LLMBackend) -> list[TierSuggestion]`; reuse `lint.collect_docs` to group bodies by type; one `llm.chat` per type.
- [x] 4.3 REFACTOR: dedupe JSON-extraction/parse helpers against `edge_typing.py`'s pattern only if it does not violate the "no cross-import of `_`-prefixed symbols" convention (design D4) — otherwise keep the module-local copy. **Result**: kept the module-local copy (design D4 forbids cross-importing `_`-prefixed symbols from `edge_typing.py`); no further extraction needed.

## Phase 5: CLI Verb — `suggest-volatility` (PR 3)

- [x] 5.1 RED: create `tests/unit/cli/test_suggest_volatility.py` (mirror `test_suggest_relations.py`) covering: require_workspace gate fires before any LLM call; `read_config` `(OSError, ValueError)` guard; 3-tier ordered `OllamaUnavailable` → `OllamaModelNotFound` → generic `OllamaError` handling, each exit 1 with zero writes; report shape (`[{tier}] {TypeName}` + `  rationale:`, or `[?]` + `  note: no valid tier suggested`); closing `Next: edit type_tiers in openkos.yaml` hint; "No concept types found." empty case; zero writes to bundle/index/log/config on a full successful run.
- [x] 5.2 GREEN: add `@app.command("suggest-volatility")` to `src/openkos/cli/main.py` (clone `suggest_relations_cmd`, main.py:2074) wired to `resolution.volatility_typing.suggest_volatility`.

## Phase 6: Verification Gate (all PRs, before each merge)

- [x] 6.1 Run `uv run pytest` — full suite green. (1387 passed)
- [x] 6.2 Run `uv run ruff check .` — clean.
- [x] 6.3 Run `uv run ruff format .` then `uv run ruff format --check .` — clean, formatted before commit.
- [x] 6.4 Run `uv run mypy .` — clean.
- [x] 6.5 Confirm no edits were made to `docs/adr/0007*` (extends, does not modify, the existing ADR). Confirmed via `git status`/`git diff` — only `src/openkos/cli/main.py` (modified) and 3 new files touched this batch.
