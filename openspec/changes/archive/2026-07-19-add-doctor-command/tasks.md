# Tasks: `add-doctor-command` — `openkos doctor` environment health check

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~548 total (`ollama.py` ~45; `test_ollama.py` ~140; `cli/main.py` ~110; `test_doctor.py` ~230 new file; `docs/cli.md` ~20; `docs/roadmap.md` ~3) |
| 400-line budget risk | High (overall); each individual PR stays under 400 |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (`llm/` leaf additions) → PR 2 (`doctor` command + docs, depends on PR 1) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending — recommend `stacked-to-main` (sequential dependency, both slices independently mergeable and revertible) |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `OllamaClient.list_models()` + module-level `model_tag_matches()` in `llm/ollama.py`, fully tested (~185 lines) | PR 1 | `uv run pytest tests/unit/llm/test_ollama.py` | N/A — pure fake-`urlopen` unit tests, no live Ollama contact (mirrors `chat()`'s existing suite; no integration harness exists for `llm/`) | `git revert`; leaf addition with zero callers until PR 2 wires it in |
| 2 | `doctor` command (`CheckResult`, `_render_check`, the 5-check accumulate-then-exit flow) + `tests/unit/cli/test_doctor.py` + docs (~363 lines) | PR 2 (base = PR 1) | `uv run pytest tests/unit/cli/test_doctor.py` | Optional manual smoke: `openkos doctor` against a real local `ollama serve`, deferred to user acceptance | `git revert`; additive command, no schema/config/state change, other commands untouched |

## Phase 1: RED — `list_models()` + `model_tag_matches()` tests
- [x] 1.1 `tests/unit/llm/test_ollama.py`: `test_list_models_returns_installed_tags_from_model_field` — fake 200 body with `model` entries → returns tag list (Scenario: Reachable server returns installed tags).
- [x] 1.2 same file: `test_list_models_falls_back_to_name_field` — entries with `name` but no `model` key → tag still returned (D2 field variance).
- [x] 1.3 same file: `test_list_models_skips_malformed_entries` — a non-dict entry or an entry with neither `model` nor `name` is skipped, not raised.
- [x] 1.4 same file: `test_list_models_unreachable_raises_ollama_unavailable` — `URLError`/`TimeoutError` from `urlopen` → `OllamaUnavailable`, no raw exception leaks.
- [x] 1.5 same file: `test_list_models_non_200_raises_ollama_error` — `HTTPError` → `OllamaError` (reusing `_map_http_error`).
- [x] 1.6 same file: `test_list_models_malformed_json_raises_ollama_error` — 200 body that is not valid JSON → `OllamaError`.
- [x] 1.7 same file: `test_model_tag_matches_exact_match` — configured equals an installed tag exactly → `True`.
- [x] 1.8 same file: `test_model_tag_matches_bare_configured_matches_latest_installed` — bare `qwen3` vs installed `qwen3:latest` → `True` (D4 symmetric normalization).
- [x] 1.9 same file: `test_model_tag_matches_no_match_returns_false` — no installed tag matches under either normalization → `False`.
- [x] 1.10 same file: `test_model_tag_matches_case_sensitive_mismatch` — differing case → `False` (D4 honest exact match).
- [x] 1.11 (added) same file: `test_list_models_body_read_failure_raises_ollama_unavailable` — a transport failure while reading the response body → `OllamaUnavailable`, symmetric with `chat()`'s body-read guard; closes a branch-coverage gap.

## Phase 2: GREEN — implement `llm/ollama.py` additions
- [x] 2.1 `src/openkos/llm/ollama.py`: add `list_models()` method per design's snippet (D1, D2) — `GET {host}/api/tags`, `_map_http_error`/`_unavailable` reuse, `model`-or-`name` field read, skip malformed entries.
- [x] 2.2 same file: add module-level `model_tag_matches(configured, installed)` (D3, D4) — bare-name-to-`:latest` normalization on both sides, case-sensitive comparison.

## Phase 3: RED — `doctor` command tests (`tests/unit/cli/test_doctor.py`, new file)
- [x] 3.1 `test_doctor_all_healthy_exits_zero` — initialized workspace, valid config, reachable Ollama, model installed, readable bundle → 5 `[PASS]` lines, exit 0 (Scenario: Healthy workspace prints all applicable checks).
- [x] 3.2 `test_doctor_ollama_down_shows_start_server_remediation` — inject fake `OllamaClient` raising `OllamaUnavailable` → `[FAIL] Ollama reachable` + `  -> ollama serve`, `[SKIP]` model check, exit 1.
- [x] 3.3 `test_doctor_missing_model_shows_pull_remediation_with_exact_tag` — reachable but tag absent → `[FAIL] Model '<tag>' installed` + `  -> ollama pull <tag>`, exit 1 (Scenario: Non-matching tag fails with pull remediation).
- [x] 3.4 `test_doctor_malformed_config_fails_and_exits_one` — write invalid `openkos.yaml` post-init → `[FAIL] Config valid`, exit 1.
- [x] 3.5 `test_doctor_outside_workspace_unhealthy_ollama_exits_one` — no workspace + Ollama unreachable → `[FAIL]` workspace (informational, init remediation), `[SKIP]` config+bundle, Ollama/model checks run against `DEFAULT_MODEL`, exit 1 (Scenario: Unhealthy pre-init environment exits one).
- [x] 3.6 `test_doctor_outside_workspace_healthy_exits_zero` — no workspace, Ollama reachable, default model installed → workspace `[FAIL]` alone (informational), overall exit 0 (Scenario: Healthy pre-init environment exits zero / Informational-only failure still exits zero).
- [x] 3.7 `test_doctor_later_check_still_prints_after_earlier_critical_failure` — Ollama down AND malformed config; assert both fail AND the bundle-readable check still renders its own result — proves accumulate-then-exit, no short-circuit (Scenario: A failing check does not stop later checks from running).
- [x] 3.8 `test_doctor_run_leaves_workspace_unchanged` — snapshot file listing/mtimes before and after a mixed-outcome run → identical; no fix command is executed by `doctor` itself (Scenario: Doctor run leaves the workspace unchanged).
- [x] 3.9 (added) `test_doctor_ollama_generic_error_fails_without_serve_remediation` — a non-transport `OllamaError` still fails the reachable check but carries no `ollama serve` remediation.
- [x] 3.10 (added) `test_doctor_bundle_findings_fail_but_stay_informational` — a bundle §9 conformance finding fails the bundle-readable check but the process still exits 0 (D7 criticality split); closes a branch-coverage gap.

## Phase 4: GREEN — implement `doctor` command (`src/openkos/cli/main.py`)
- [x] 4.1 Add `from dataclasses import dataclass` import; add `model_tag_matches` to the existing `from openkos.llm.ollama import (...)` block.
- [x] 4.2 Add frozen `CheckResult` dataclass (`label`, `status`, `critical`, `remediation`, `detail`) and `_render_check` helper (D5) per design's exact output format.
- [x] 4.3 Implement `@app.command() def doctor()` per design's skeleton: workspace-initialized (info) → config-valid (critical, skip outside workspace) → Ollama-reachable via `OllamaClient(model=model).list_models()` (critical) → model-installed via `model_tag_matches` (critical, `[SKIP]` blocked when unreachable, D6) → bundle-readable (info, skip outside workspace); render every result unconditionally; `raise typer.Exit(code=1)` iff any critical check failed (D7).

## Phase 5: Docs
- [x] 5.1 `docs/cli.md`: add `### openkos doctor` section — the 5 checks, `[PASS]`/`[FAIL]`/`[SKIP]` format, remediation lines, criticality split, outside-workspace behavior, exit-code contract; update the command list count.
- [x] 5.2 `docs/roadmap.md`: add `doctor` to the MVP-1 command list (6→7).

## Phase 6: Verification Gate
- [x] 6.1 `uv run pytest --cov` — full suite green; ≥90% branch on changed lines.
- [x] 6.2 `uv run ruff check .` && `uv run ruff format --check .` — clean.
- [x] 6.3 `uv run mypy .` — clean (strict).
</content>
