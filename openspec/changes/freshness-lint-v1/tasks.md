# Tasks: Freshness Lint v1 — Slice 1 (Volatility Taxonomy + Volatility-Aware Windows)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~450-550 (prod ~130-150, tests ~250-300, docs/config ~30) |
| 400-line budget risk | Medium |
| Chained PRs recommended | No |
| Suggested split | Single PR (slice already minimal; splitting registry/config/lint would break the load-bearing resolver's test cohesion) |
| Delivery strategy | auto-forecast (800-line budget) |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Medium

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Full slice: registry tiers → config windows → LintDoc field → resolver → check_stale_stamps/CLI wiring → spec sync | PR 1 (only) | `uv run pytest tests/unit/model/test_types.py tests/unit/test_config.py tests/unit/test_lint.py tests/unit/cli/test_lint.py` | `openkos lint` against a workspace fixture with mixed `volatility:`/type-default concepts | Revert PR; `volatility:` frontmatter and `volatility_windows:` config become inert, lint reverts to single global `freshness_window` |

## Phase 1: Registry — Per-Type Default Volatility (types.py)

- [x] 1.1 RED: `tests/unit/model/test_types.py` — table test asserting `ObjectType.default_volatility` per name (`static`={Place,Event,Decision,Source}, `slow`={Concept,Entity,Person,Organization}, `volatile`={Procedure,Project}); assert `VOLATILITY_TIERS == {"static","slow","volatile"}`; assert `TYPE_TO_DEFAULT_VOLATILITY` matches registry. Run `uv run pytest tests/unit/model/test_types.py -k volatility` — confirm RED (AttributeError/NameError).
- [x] 1.2 GREEN: `src/openkos/model/types.py` — add `default_volatility: str` to `ObjectType`; update all 10 `REGISTRY` entries with their tier; add `VOLATILITY_TIERS: frozenset[str]` and `TYPE_TO_DEFAULT_VOLATILITY: dict[str,str]`. Run `uv run pytest tests/unit/model/test_types.py` — confirm GREEN; confirm no other `REGISTRY`/`ObjectType(...)` call site broke (positional-arg registry entries).
- [x] 1.3 REFACTOR: `uv run ruff check src/openkos/model/types.py && uv run mypy src/openkos/model/types.py`.

## Phase 2: Config — `volatility_windows` (config.py)

- [x] 2.1 RED: `tests/unit/test_config.py` — `DEFAULT_VOLATILITY_WINDOWS == {"slow":"90d","volatile":"7d"}`; `Config.volatility_windows` defaults to `{}` when key absent/null; a present `volatility_windows:` map passes through verbatim (mirrors existing `freshness_window` table-test pattern). Run `uv run pytest tests/unit/test_config.py -k volatility_windows` — confirm RED.
- [x] 2.2 GREEN: `src/openkos/config.py` — add `DEFAULT_VOLATILITY_WINDOWS`; add `volatility_windows: dict[str,str]` field to `Config`; in `read_config`, `raw.get("volatility_windows")` with `is not None` fallback to `{}` (grammar parsing stays out of config, per design). Run `uv run pytest tests/unit/test_config.py` — confirm GREEN.
- [x] 2.3 `src/openkos/templates/openkos.yaml.template` — add commented `volatility_windows:` block (`slow: 90d`, `volatile: 7d`) below `freshness_window: 7d`; keep `freshness_window` line unchanged (legacy fallback). Run `uv run pytest tests/unit/test_config.py -k template` if such a byte-match test exists.
- [x] 2.4 REFACTOR: `uv run ruff check src/openkos/config.py && uv run mypy src/openkos/config.py`.

## Phase 3: LintDoc `type`/`volatility` Fields (lint.py)

- [x] 3.1 RED: `tests/unit/test_lint.py` — extend `_doc()` helper with `type`/`volatility` kwargs (default `""`); add `test_collect_docs_reads_type_and_volatility_from_frontmatter` and `test_collect_docs_defaults_type_and_volatility_to_empty_when_absent`. Run targeted test — confirm RED (`LintDoc` has no such fields).
- [x] 3.2 GREEN: `src/openkos/lint.py` — add `type: str` and `volatility: str` to `LintDoc`; populate both via `metadata.get(..., "")` in `collect_docs`. Run `uv run pytest tests/unit/test_lint.py -k collect_docs` — confirm GREEN.
- [x] 3.3 RED→GREEN regression: add `test_build_concept_does_not_emit_volatility` (and source variant) asserting ingest output has no `volatility` key — pins the "ingest stays byte-stable" contract; confirm it passes with ZERO changes to `build_concept`/`build_source_concept`.

## Phase 4: Never-Raising Resolution Algorithm (lint.py — load-bearing)

- [x] 4.1 RED: `tests/unit/test_lint.py` — exhaustive table tests for `resolve_windows(cfg)`: valid `slow`/`volatile` map values resolve correctly; absent key falls to `DEFAULT_VOLATILITY_WINDOWS[tier]`; malformed tier value degrades to `DEFAULT_FRESHNESS_WINDOW` + notice (reuses `resolve_window`); non-map/`null` `volatility_windows` treated as empty (all tier defaults); never raises. Confirm RED (`resolve_windows`/`VolatilityWindows` undefined).
- [x] 4.2 RED: table tests for `window_for_doc(doc, windows)`: per-concept override wins over type default; absent `volatility` → per-type default; unknown `volatility` value → per-type default (degrade); unknown/absent `type` + no override → `windows.fallback`; `static` tier (by override or type) → `None`. Confirm RED.
- [x] 4.3 GREEN: `src/openkos/lint.py` — add frozen `VolatilityWindows(slow, volatile, fallback: timedelta)`; implement `resolve_windows(cfg) -> tuple[VolatilityWindows, list[str]]` and `window_for_doc(doc, windows) -> timedelta | None` per precedence (per-concept → per-type registry → fallback; `static`→`None`). Run both targeted test files — confirm GREEN.
- [x] 4.4 REFACTOR: `uv run ruff check src/openkos/lint.py && uv run mypy src/openkos/lint.py`.

## Phase 5: `check_stale_stamps` + CLI Wiring

- [x] 5.1 RED: update every existing `check_stale_stamps(docs, today=..., window=timedelta(...))` call site in `tests/unit/test_lint.py` to `windows=VolatilityWindows(...)` — confirm these now FAIL against the current `window: timedelta` signature (signature-change RED).
- [x] 5.2 RED: add new scenarios — static-tier concept with an ancient stamp is never flagged; per-concept override (`volatility: static` on a `Procedure`) beats type default; `slow`-tier type default wins over a shorter global fallback; unresolvable volatility (unknown type + invalid `volatility`) degrades to fallback window and never raises.
- [x] 5.3 GREEN: `src/openkos/lint.py` — change `check_stale_stamps(docs, *, today, windows: VolatilityWindows)`; resolve per-doc via `window_for_doc`, skip when `None`. Run `uv run pytest tests/unit/test_lint.py` — confirm full GREEN.
- [x] 5.4 RED: `tests/unit/cli/test_lint.py` — update/add a test asserting the CLI calls `resolve_windows(cfg)` (not the old single `resolve_window(cfg.freshness_window)`), passes `windows` to `check_stale_stamps`, and surfaces any `resolve_windows` notices alongside the existing `skip_notices`.
- [x] 5.5 GREEN: `src/openkos/cli/main.py::lint` — replace `resolve_window(cfg.freshness_window)` with `resolve_windows(cfg)`; pass `windows=windows` to `check_stale_stamps`; extend `notices` with `resolve_windows`'s notice list. Run `uv run pytest tests/unit/cli/test_lint.py` — confirm GREEN.
- [x] 5.6 REFACTOR: `uv run ruff check src/openkos/cli/main.py && uv run mypy src/openkos/cli/main.py`.

## Phase 6: Canonical Spec Sync

- [x] 6.1 `openspec/specs/lint/spec.md` — apply the Non-Goals Update verbatim from the change delta (`openspec/changes/freshness-lint-v1/specs/lint/spec.md`): replace "volatility classification via the `freshness` field (lint never reads it)" with the orthogonal-skip-flag wording; keep every other Non-Goal line unchanged.
- [x] 6.2 `openspec/specs/lint/spec.md` — merge the MODIFIED "Stale-Stamp Scan" requirement (volatility-resolved window, `static` never flagged) so the canonical spec matches shipped behavior; do NOT touch Orphan-Page/Non-Gating/Read-Only requirements.

## Phase 7: Full Verify Gate

- [x] 7.1 `uv run pytest` — full suite green.
- [x] 7.2 `uv run ruff format .` — apply formatting.
- [x] 7.3 `uv run ruff format --check .` — confirm no diff after apply.
- [x] 7.4 `uv run ruff check .` — clean.
- [x] 7.5 `uv run mypy .` — clean.
- [x] 7.6 Confirm ADR-0007 (`docs/adr/0007-volatility-taxonomy.md`) status unchanged (`Proposed`) and `docs/adr/README.md` index entry already present — no edit needed unless implementation diverged from the ADR's decision.

## Files Touched

`src/openkos/model/types.py`, `src/openkos/config.py`, `src/openkos/lint.py`, `src/openkos/cli/main.py`, `src/openkos/templates/openkos.yaml.template`, `openspec/specs/lint/spec.md`, `tests/unit/model/test_types.py`, `tests/unit/test_config.py`, `tests/unit/test_lint.py`, `tests/unit/cli/test_lint.py`.
