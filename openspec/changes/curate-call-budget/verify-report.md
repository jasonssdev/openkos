```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:98ba0c1f807d011a68ec472276bd
verdict: pass
blockers: 0
critical_findings: 0
requirements: 7/7
scenarios: 18/18
test_command: uv run pytest
test_exit_code: 0
test_output_hash: sha256:cb9df6320f55d67b26d376827e2e5a531c72edfd67e9cb624246b13066fd1908
build_command: uv run ruff check . && uv run ruff format --check . && uv run mypy .
build_exit_code: 0
build_output_hash: sha256:82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18
```

## Verification Report

**Change**: curate-call-budget (#382)
**Version**: N/A (delta specs, entity-resolution + curate-command)
**Mode**: Strict TDD
**Branch inspected**: `curate-call-budget-slice-b` (stacked on `curate-call-budget-slice-a`, off `main`)
**Commits**: Slice A `f807d01`, `1a68ec4`; Slice B `72276bd`, `98ba0c1`

### Completeness
| Metric | Value |
|--------|-------|
| Tasks total | 29 |
| Tasks complete | 29 |
| Tasks incomplete | 0 |

All 29 tasks re-audited against actual disk state, not trusted from the checkbox alone (see Correctness table below for the highest-risk items).

### Build & Tests Execution

**Tests**: `uv run pytest` → ✅ **3577 passed** in 130.67s. Confirms the apply agent's reported count exactly.

**Ruff**: `uv run ruff check .` → ✅ All checks passed. `uv run ruff format --check .` → ✅ 171 files already formatted.

**Mypy**: `uv run mypy .` (strict) → ✅ Success: no issues found in 171 source files.

**Coverage**: `uv run pytest --cov` → ✅ **97.29%** total (gate `fail_under = 90`, branch coverage). `resolution/candidates.py`: 120 stmts, 0 missed, 34 branches, 0 missed → **100%**. `resolution/__init__.py`: 100%. All three quality-gate numbers reported by the apply agent (3577 passed, ruff/mypy clean, 97.29% coverage) are confirmed by independent re-execution, not merely trusted.

### Spec Compliance Matrix

**entity-resolution/spec.md**

| Requirement | Scenario | Test | Result |
|-------------|----------|------|--------|
| Bounded Candidate-Group Output Per Call | Adjudication call count never exceeds the cap | `test_candidates.py::test_over_cap_module_scoped_fixture_retains_exactly_the_cap` | ✅ COMPLIANT |
| Bounded Candidate-Group Output Per Call | Below-cap corpus is unaffected | `test_candidates.py::test_below_cap_produced_equals_retained_and_groups_match_uncapped` | ✅ COMPLIANT |
| Deterministic Ranking For Truncation | HIGH fills before LOW, global tier priority | `test_candidates.py::test_high_fills_before_low_global_tier_priority` | ✅ COMPLIANT |
| Deterministic Ranking For Truncation | HIGH-only excess tie-broken by (okf_type, member_ids); LOW ties | `test_candidates.py::test_high_only_excess_tie_broken_by_okf_type_member_ids`, `test_low_tier_ties_broken_deterministically` | ✅ COMPLIANT |
| Deterministic Ranking For Truncation | Retained groups keep canonical order; repeated calls identical | `test_candidates.py::test_retained_slice_is_canonical_order_and_calls_are_deterministic` | ✅ COMPLIANT |
| Truncation Is Never Silent | Cap binds — produced/retained observable | `test_candidates.py::test_candidate_group_truncation_notice_names_both_counts_above_cap` | ✅ COMPLIANT |
| Truncation Is Never Silent | Cap does not bind — produced == retained | `test_candidates.py::test_candidate_group_truncation_notice_none_below_cap` | ✅ COMPLIANT |
| ACRONYM Once-Under-The-Stronger-Tier Preserved | Dedup holds when cap engaged | `test_candidates.py::test_acronym_low_dedup_holds_when_cap_engaged` | ✅ COMPLIANT |

**curate-command/spec.md**

| Requirement | Scenario | Test | Result |
|-------------|----------|------|--------|
| Identity Stage Reuses Merge Cores | Identity's adjudication call count stays capped on a large corpus | `test_curate.py` (RED tests 15-16, over-cap notice + `llm_calls == 50`) | ✅ COMPLIANT |
| Identity Cost Line Discloses Truncation | Cap reached — notice discloses both counts | `test_curate.py` (over-cap Identity probe test) | ✅ COMPLIANT |
| Identity Cost Line Discloses Truncation | Cap not reached — no notice | `test_curate.py` (below-cap Identity probe test) | ✅ COMPLIANT |
| Below-Cap Cost-Line Output Is Byte-Identical | Below-cap Identity cost line unchanged | `test_curate.py` (below-cap wording assertion, pinned literal untouched) | ✅ COMPLIANT |
| Below-Cap Cost-Line Output Is Byte-Identical | Above-cap cost line reflects bounded count | `test_curate.py` (over-cap probe, `llm_calls == report.retained`) | ✅ COMPLIANT |
| (entity-resolution, transitively) Truncation Is Never Silent — `duplicates` caller | Over-cap emits notice / below-cap emits nothing | `test_duplicates.py::test_duplicates_over_cap_bundle_emits_the_truncation_notice`, `test_duplicates_below_cap_bundle_emits_no_truncation_notice` | ✅ COMPLIANT |
| (entity-resolution, transitively) Truncation Is Never Silent — `adjudicate` caller | Over-cap emits notice / below-cap emits nothing | `test_adjudicate.py` (new RED tests 25-26, over-cap/below-cap) | ✅ COMPLIANT |

**Compliance summary**: 18/18 scenarios compliant (7 requirements across both spec deltas).

### Correctness (Static Evidence) — hard-check items

| Item | Status | Notes |
|------|--------|-------|
| 1. Cap binds before any LLM call | ✅ Confirmed | `_MAX_CANDIDATE_GROUPS = 50` (`candidates.py:87`) applied inside `find_candidates_report` to the FULL cross-type set; `sorted(groups, key=_cap_rank_key)[:_MAX_CANDIDATE_GROUPS]`, then re-sorted into canonical order (`candidates.py:365-372`). `test_over_cap_module_scoped_fixture_retains_exactly_the_cap` uses real files via `tmp_path_factory` (not a monkeypatched constant), 60 HIGH groups in, exactly 50 retained. `_identity_probe` sets `llm_calls=report.retained`. |
| 2. Truncation never silent, all 3 callers | ✅ Confirmed | `curate.py:282-289` (`_identity_probe`), `main.py:7744-7750` (`duplicates`), `main.py:7934-7940` (`adjudicate`) all call `find_candidates_report`, then `candidate_group_truncation_notice(report)`, and print/set the notice iff not `None`. All three verified with passing over-cap/below-cap tests. |
| 3. Below-cap output byte-identical to pre-change | ✅ Confirmed | `git diff main...curate-call-budget-slice-b` on `curate.py`/`main.py`/test files shows zero deleted pinned-literal, stdout, or prompt-sequence lines — only docstrings, notice wiring, and mechanical monkeypatch-target renames (`find_candidates` → `find_candidates_report`, `return [...]` → `return CandidateGroupReport(...)`). No assertion contract was edited to make a test pass. |
| 4. 53 (+1 correction = 54 real sites, task count matches) monkeypatch conversions complete | ✅ Confirmed, none missed | Multi-line-aware AST-style audit of every `monkeypatch.setattr(...)` call in the four test files found **zero** remaining bare `find_candidates` targets (string-literal or attribute-object form) — every `find_candidates`-related patch site targets `find_candidates_report`. Counts: `test_curate.py` 15 total find_candidates-related setattr calls (13 conversions + 2 new RED tests), `test_adjudicate.py` 49 (47+2), `test_duplicates.py` 7 (5+2), `test_confidential_local_exemption.py` 1 — all reconcile exactly against the task brief's stated conversion counts plus the new RED tests added alongside them. |
| 5. Task 13 docstring-truth amendment | ✅ Confirmed present | `find_exact_title_groups`'s docstring (`candidates.py:405-429`) now states the equivalence "holds verbatim only while the cap does not bind" and that "`find_candidates`'s retained HIGH set is always a PREFIX of this function's (uncapped) output, in the SAME order" — exactly the language the task specified. This item is genuinely enforced by no pinned test other than the new `test_high_slice_is_a_prefix_of_find_exact_title_groups`, which exists. |
| 6. `_cap_rank_key` tier branch | ✅ Confirmed | `candidates.py:132`: `score = float(group.trigger) if group.tier is Tier.LOW else 0.0`. Covered by `test_high_fills_before_low_global_tier_priority` (HIGH/LOW mixed corpus, exercises the non-LOW `0.0` branch) and the below-cap/HIGH-only-excess tests (exercise both branches without raising). |
| 7. Cap framed as safety rail, not session budget | ✅ Confirmed | `candidates.py:93-97` docstring: "This is a SAFETY RAIL against a pathological corpus, NOT a per-session curation budget... MUST NOT be retuned into an iterative-curation mechanism." No code or docstring in the diff reframes it otherwise. |

### Coherence (Design)
| Decision | Followed? | Notes |
|----------|-----------|-------|
| D1 — `find_candidates_report` sibling entry point, `find_candidates` delegates | ✅ Yes | `candidates.py:456-471`: `find_candidates` = `list(find_candidates_report(...).groups)`. |
| D2 — New `CandidateGroupReport`, not reused `sqlite_graph.CandidateReport` | ✅ Yes | `candidates.py:105-121`. |
| D3 — `candidate_group_truncation_notice` lives in `resolution/candidates.py`, not `cli/curate.py` | ✅ Yes | Confirmed at `candidates.py:139-150`; imported by both `curate.py` and `main.py`. |
| D4 — Rank key tier branch mandatory | ✅ Yes | See item 6 above. |
| D5 — HIGH/ACRONYM ties reuse `(okf_type, member_ids)` | ✅ Yes | `_cap_rank_key` returns that tuple as its trailing tie-break. |
| D6 — Retained slice re-sorted into canonical order before returning | ✅ Yes | `candidates.py:370-372`. |
| D7 — No ADR | ✅ Yes | No `docs/adr/` file added in the diff. |

### TDD Compliance
| Check | Result | Details |
|-------|--------|---------|
| TDD Evidence reported | ✅ | apply-progress artifact (#2453) documents RED/GREEN per task, cross-checked against actual test/production diffs. |
| All tasks have tests | ✅ | 29/29 — cap/ranking/report (8 resolution tests), Identity disclosure (2 CLI tests), `duplicates`/`adjudicate` disclosure (2+2 CLI tests), plus 54 mechanical monkeypatch conversions verified live-patching. |
| RED confirmed (tests exist) | ✅ | All new test functions present and enumerated above. |
| GREEN confirmed (tests pass) | ✅ | Full suite re-run independently: 3577/3577 passed. |
| Triangulation adequate | ✅ | Over-cap/below-cap pairs for every disclosing caller; 6 distinct ranking scenarios for the tie-break/ordering requirement. |
| Safety Net for modified files | ✅ | Full-suite pass (3577) covers all modified production files; no regressions in unrelated modules. |

**TDD Compliance**: 6/6 checks passed

### Assertion Quality
No tautologies, ghost loops, or ineffective assertions found in the sampled new tests (`test_duplicates_over_cap_bundle_emits_the_truncation_notice`, `test_duplicates_below_cap_bundle_emits_no_truncation_notice`, `test_over_cap_module_scoped_fixture_retains_exactly_the_cap`, `test_below_cap_produced_equals_retained_and_groups_match_uncapped`). Each asserts exact `stderr`/count values against production-code output, not vacuous type-only checks.

**Assertion quality**: ✅ All assertions verify real behavior

### Changed File Coverage
| File | Line % | Branch % | Rating |
|------|--------|----------|--------|
| `src/openkos/resolution/candidates.py` | 100% | 100% | ✅ Excellent |
| `src/openkos/resolution/__init__.py` | 100% | 100% | ✅ Excellent |
| `src/openkos/cli/curate.py` | not individually broken out; whole-suite pass, no new uncovered lines reported | — | ✅ (aggregate) |
| `src/openkos/cli/main.py` | not individually broken out; whole-suite pass, no new uncovered lines reported | — | ✅ (aggregate) |

**Average changed file coverage**: candidates.py/`__init__.py` both 100%; aggregate repository coverage 97.29% with no new gate-failing files.

### Quality Metrics
**Linter**: ✅ No errors
**Type Checker**: ✅ No errors (strict)

### Issues Found
**CRITICAL**: None
**WARNING**: None
**SUGGESTION**: None. (One minor observation for the orchestrator's own bookkeeping, not a defect: `openspec/changes/surface-merged-body-contradictions/` is an untracked, unrelated directory present in the working tree — outside this change's scope, noted only for completeness.)

### Verdict
**PASS**

All 29 tasks are genuinely complete on disk, not merely checked off. The 50-group safety-rail cap binds inside `find_candidates_report` before any adjudication call, is deterministically ranked and re-sorted into canonical order, and is disclosed via `candidate_group_truncation_notice` by all three callers (`curate`'s Identity stage, `duplicates`, `adjudicate`). Below-cap behavior is byte-identical to pre-change output — verified by diffing production and test files against `main` and finding zero deleted pinned-literal/assertion lines. All 54 monkeypatch conversion sites (string-literal and attribute-object forms) were audited exhaustively via a multi-line-aware scan; none were missed. The `find_exact_title_groups` docstring-truth amendment (task 13, enforced by no pinned test) is genuinely present. The rank key's tier branch avoids the `ValueError` the design warned about and is test-covered. The cap remains correctly framed as a safety rail, never reframed as a curation budget. Independent re-execution of `uv run pytest`, `ruff check`/`format --check`, `mypy` (strict), and `pytest --cov` reproduces the apply agent's reported numbers exactly (3577 passed, lint/type clean, 97.29% coverage).
