## Verification Report: freshness-lint-v1

**Mode**: Strict TDD, full artifact set (spec + design + tasks + apply-progress), gate re-run independently (not trusting reported numbers).

### Task Completeness
28/28 tasks checked `[x]` across 7 phases (Registry / Config / LintDoc fields / Resolver / check_stale_stamps+CLI / Canonical Spec Sync / Verify Gate). Task count verified by direct enumeration against `openspec/changes/freshness-lint-v1/tasks.md`. Files-touched list matches `git show d0b7e79 --stat` exactly: `src/openkos/model/types.py`, `src/openkos/config.py`, `src/openkos/lint.py`, `src/openkos/cli/main.py`, `src/openkos/templates/openkos.yaml.template`, `openspec/specs/lint/spec.md`, `tests/unit/model/test_types.py`, `tests/unit/test_config.py`, `tests/unit/test_lint.py`, `tests/unit/cli/test_lint.py`, `tests/unit/model/test_okf.py`, plus ADR-0007 + README index + SDD planning artifacts (design.md, proposal.md, tasks.md, delta specs). No extra production files touched.

### Build/Test Evidence (independently re-run, not trusted from apply-progress)
- `uv run pytest -q`: **1337 passed**, 0 failed, exit code 0 (matches apply-progress's reported 1337).
- `uv run ruff check .`: **All checks passed!**, exit code 0.
- `uv run ruff format --check .`: **102 files already formatted**, no drift, exit code 0.
- `uv run mypy .`: **Success: no issues found in 102 source files**, exit code 0.
- `git show d0b7e79 --stat`: 18 files changed, 1357 insertions(+), 60 deletions(-) (includes SDD planning docs + ADR). Code/test/canonical-spec-only subset: 873 changed lines (per apply-progress's own `git diff --shortstat` note) — exceeds the shared-conventions 400-line default budget and the tasks.md forecast's stated 800-line ceiling. See Issues below.

### Spec Requirement Compliance (source-inspected + test-covered)

**Domain: concept-volatility (new capability, 4 requirements / 6 scenarios)**

| Requirement / Scenario | Evidence | Status |
|---|---|---|
| Fixed Three-Tier Taxonomy — only 3 valid tiers | `tests/unit/model/test_types.py::test_volatility_tiers_is_the_closed_three_value_set` | PASS |
| Per-Concept override — explicit override wins | `tests/unit/test_lint.py::test_window_for_doc_precedence_table` row `("Concept","volatile",...)` | PASS |
| Per-Concept override — absent field falls through | `test_window_for_doc_precedence_table` rows `("Procedure","",...)`, `("Concept","",...)` | PASS |
| Per-Type Default Registry — type default applies | `tests/unit/model/test_types.py::test_registry_default_volatility_per_type` + `test_type_to_default_volatility_matches_registry` | PASS |
| Never-Raising Resolution — invalid/unknown degrades | `test_window_for_doc_precedence_table` rows `("Person","not-a-tier",...)`, `("SomeUnknownType","",...)`; `test_resolve_windows_never_raises` | PASS |
| Never-Raising Resolution — full fallback to global window | `test_window_for_doc_precedence_table` row `("","",_WINDOWS.fallback)`; `test_resolve_windows_fallback_uses_freshness_window` | PASS |

**Domain: lint (MODIFIED requirement, 1 requirement / 8 scenarios)**

| Scenario | Evidence | Status |
|---|---|---|
| Stale beyond resolved window flagged | `test_check_stale_stamps_flags_a_stamp_beyond_the_window`, `test_check_stale_stamps_slow_tier_still_flags_beyond_its_own_window` | PASS |
| Fresh within resolved window not flagged | `test_check_stale_stamps_does_not_flag_a_stamp_within_the_window`, `test_check_stale_stamps_exact_boundary_is_not_stale` | PASS |
| static-tier never flagged | `test_check_stale_stamps_static_tier_never_flagged` | PASS |
| Per-concept override wins over type default | `test_check_stale_stamps_per_concept_override_beats_type_default` (Procedure + `volatility: static`) | PASS |
| Type default wins over global fallback | `test_check_stale_stamps_slow_tier_default_wins_over_shorter_fallback` | PASS |
| Pure-ingest bundle zero findings | `test_check_stale_stamps_pure_ingest_bundle_has_zero_findings` | PASS |
| Snapshot doc with embedded stamp-shaped string not flagged | `test_check_stale_stamps_skips_snapshot_docs_with_stamp_shaped_text` | PASS |
| Unresolvable volatility degrades to fallback, never raises | `test_check_stale_stamps_unresolvable_volatility_degrades_to_fallback`, `test_resolve_windows_never_raises` | PASS |

CLI-wiring functional (zero-mock) confirmation: `tests/unit/cli/test_lint.py::test_lint_wires_volatility_windows_into_the_stale_scan` (slow-tier 30d-old stamp not flagged — proves the CLI actually reaches `resolve_windows`, not the old global 7d) and `test_lint_surfaces_a_notice_on_malformed_volatility_window_config`. Ingest byte-stability pinned by `tests/unit/model/test_okf.py::test_build_concept_emits_no_volatility_key` and `test_build_source_concept_emits_no_volatility_key`.

All 5 requirements / 14 scenarios across both delta specs map to a real, passing test. `test_window_for_doc_precedence_table` is an 11-row exhaustive parametrize table independently confirmed to cover every precedence branch (override-wins, absent-falls-through, unknown-degrades, unknown-type-fallback, static-never-flagged via both override and type default).

### No-Drift / No-Over-Reach Confirmation
- Changed files limited to exactly the expected set: `types.py`, `config.py`, `lint.py`, `cli/main.py`, the yaml template, the 5 test files, canonical `openspec/specs/lint/spec.md`, ADR-0007 + README index row, plus SDD planning docs. No other production module touched.
- `lint.py` read-only / never-fail / deterministic-clock v0 contract preserved: `check_stale_stamps` and `resolve_windows`/`window_for_doc` remain pure functions of injected `today`/`windows`/doc data; no new I/O, no new exception paths (confirmed by direct read of `lint.py:190-254` and the `never_raises` test).
- Registry (`types.py`) stays a zero-dependency leaf — `default_tier` is a plain string field, no new imports.
- Ingest (`build_concept`/`build_source_concept`) confirmed unchanged and byte-stable — no `volatility` key emitted, per direct read and pinning regression tests.
- ADR-0007 present, `status: Proposed`, indexed in `docs/adr/README.md` row 0007. The README's incidental ADR-0006 backfill (row was previously missing) verified correct against `docs/adr/0006-default-embedding-model.md`'s actual `status: Accepted` — not corrupting, a legitimate prior-gap fix.
- Canonical `openspec/specs/lint/spec.md` Non-Goals Update and Stale-Stamp Scan requirement merge verified word-for-word consistent with the change delta spec.

### Issues
- CRITICAL: 0
- WARNING: 1 — Review-budget overrun: code+test+canonical-spec diff is 873 changed lines, above the shared-conventions 400-line default budget and above the tasks.md forecast's own stated 800-line ceiling (forecast: ~450-550, actual: 873; already flagged in apply-progress). No `size:exception` was requested and no PR/chaining was used. Recommend the orchestrator apply the High-risk (>400 lines) 4R full review-lens sweep rather than a single dominant-risk lens, given the load-bearing resolver touches behavior/state/determinism (`review-reliability`) and precedence/degrade logic (`review-risk`).
- SUGGESTION: 0

### Verdict: **PASS WITH WARNINGS**
28/28 tasks complete and reflected exactly in code. 1337/1337 tests pass (re-run independently, exit 0). `ruff check`, `ruff format --check`, and `mypy` all clean (exit 0). All 5 requirements / 14 scenarios across both delta specs (`concept-volatility` new capability + `lint` MODIFIED requirement) have a real, passing covering test, including an exhaustive 11-row precedence table. No spec drift, no over-reach, ingest byte-stability and lint's v0 read-only/never-fail contract both preserved. The single WARNING is the review-budget overrun already self-flagged in apply-progress — not a correctness defect, but the orchestrator should route this through the High-risk full 4R review lens set (not a single dominant lens) before archive.
