## Verification Report: union-judge-extraction

**Mode**: full artifacts (proposal/specs/design/tasks/apply-progress all present). Strict TDD active.

### Completeness (tasks.md)
49/49 tasks checked, including correction pair 2.23-2.24 and Phase 5 gate 5.1-5.3. All deliverables confirmed present on disk (not just claimed):
- `src/openkos/extraction/judge.py` (Phase 1 module) — exists, wired.
- `extract_concept_union` in `src/openkos/extraction/concept.py` — exists with `_UNION_BACKSTOP=12`, `judge_status` ("skipped"/"ok"/"failed"/"empty"), empty-admission floor at line 879 (`if not admitted and judge_input:`).
- `src/openkos/config.py` — `DEFAULT_UNION_JUDGE=True`, `union_judge: bool` field, `is not None` + isinstance guard (lines 654-731).
- `src/openkos/cli/main.py` — `_judge_failure_notice()` (line 1769) covers both "failed" and "empty" with distinct wording; `_stage_derived_objects(union_judge: bool=False, ...)`.
- `evals/extraction_cap/run_cap_eval.py`, `evals/decision_extraction/scripts/run_type_coverage.py` — updated, `#458`→`#456` corrected (grep confirms zero stray `#458` in code/docs; only remaining hit is tasks.md prose describing the correction task itself, which is correct).
- `evals/extraction_cap/report.md`, `evals/decision_extraction/report.md` — both contain the 2026-08-07 gate entries with the exact numbers cited in the brief (prose recall 0.84/0.97/0.80 vs baselines 0.71/0.88/0.73, cap_cost 0.00; AMI TS3005a.transcript 1,1,4, TS3005b.transcript 5,1,7, type coverage held; follow-up #457 documented).

### Spec compliance — extraction-union-judge

| Requirement | Test(s) | Status |
|---|---|---|
| Union construction below chunk threshold | `test_concept.py` (2.1/2.2 tests, `_SequencedLLM` 2-call test) | PASS |
| Chunked sources judge-only, no 2nd pass | `test_concept.py` (2.7/2.8, call-count assertion) | PASS |
| Per-run twin-drop + richer-body merge | `test_concept.py` (2.3-2.6) | PASS |
| Judge selection over closed candidate list | `test_judge.py` (1.3-1.6), `test_concept.py` (2.9-2.14, Procedure re-admission) | PASS |
| Judge failure fails closed to backstopped union | `test_concept.py` (2.15/2.16, OllamaError/empty/unparseable) | PASS |
| **Valid selection admitting zero objects degrades the same way** (mid-change scenario) | `test_union_valid_empty_selection_degrades_to_the_full_backstopped_union` (test_concept.py:2151) + `test_ingest_judge_empty_admission_keeps_the_merged_union_and_reports_distinctly` (test_ingest.py:1242) — both re-run standalone and PASS | PASS |
| Backstop cap applied once, after judge | `test_concept.py` (2.17/2.18, order-dependent test) | PASS |
| Run/judge bookkeeping on ExtractionReport | `test_concept.py` (2.21, field assertions) | PASS |
| Opt-out config flag, default ON, byte-identical fallback | `test_ingest.py::test_stage_derived_objects_union_judge_false_calls_extract_concept_once` (line 1172) confirms exactly one `extract_concept` call, no judge call when disabled | PASS |

### Spec compliance — ingestion (delta)

| Requirement | Test(s) | Status |
|---|---|---|
| Bounded/dedup derived-object staging, backstop 12 | `test_ingest_union_judge_backstop_writes_no_more_than_12_derived_objects` (line 1293) | PASS |
| Slug collision / disambiguation rules unchanged | pre-existing staging tests, untouched, still green | PASS |
| Chunked-source candidates feed staging unchanged | covered by 2.7/2.8 chunk tests + shared staging path (no chunk-specific branch found in code) | PASS |
| Judge-failure degrade reported, ingest still succeeds exit 0 | `test_ingest.py` (3.6, distinct stderr notice + exit 0) | PASS |
| Full LLM unavailability still Source-only fallback | `test_ingest.py` (3.8/3.9, base-extraction-failure path unchanged) | PASS |
| Pre-Archive Measurement Gate: recall not regressed | `evals/extraction_cap/report.md` + `evals/decision_extraction/report.md`, 2026-08-07 entries — closed PASS by maintainer, prose recall improved on all 3 fixtures, AMI type coverage held | PASS |

No spec scenario found without a covering, passing test.

### Runtime evidence
- `uv run pytest -q` → **3839 passed** in 115.61s (matches expected count exactly).
- `uv run ruff check .` → **All checks passed!**
- `uv run mypy .` → **Success: no issues found in 175 source files**.
- Targeted re-run of the two mid-change-scenario tests in isolation: both PASS.
- `git diff main -- src/openkos/extraction/concept.py` shows no hunk touching `_SYSTEM_PROMPT` — byte-identical to main, confirming task 2.22's regression claim.
- Grep confirms zero live `#458` references remain in code/docs (only tasks.md's own historical task description mentions the string, which is correct/expected).

### Design coherence
Design.md's D9 (config flag), D7 (broad-except-with-rationale in judge.py), and the union/backstop/judge architecture all match implementation 1:1. No deviations found.

### Consistency check
- **WARNING (resolved during this verify pass)**: `state.yaml` still listed tasks 5.1-5.3 as `pending` and `verify_report: false` despite `tasks.md` marking all 49 tasks complete and apply-progress/eval reports confirming the Phase 5 gate was closed PASS by the maintainer on 2026-08-07. This is a bookkeeping drift, not a code defect — the gate itself ran and passed; only the state file lagged. Corrected in this pass: `phase: verify`, `verify_report: true`, tasks 5.1-5.3 moved to `completed`, `pending: []`.
- No other inconsistencies found. `#458`/`#456` fully reconciled. Working tree still uncommitted, consistent with the batch-2 apply-progress note.

### Issues

**CRITICAL**: none.

**WARNING**: none outstanding (state.yaml drift found and corrected during this verify pass, see above).

**SUGGESTION**:
1. Follow-up #457 (judge selectivity on meeting transcripts, `TS3005b.transcript` 5/1/7 spread) is explicitly out of scope for this change and correctly deferred — no action needed here, just confirming it's tracked, not silently dropped.
2. Consider a future cleanup task to sweep the four batch-1 files still containing legacy `#458` references outside this change's declared scope (`src/openkos/config.py`, `src/openkos/cli/main.py`, `src/openkos/extraction/concept.py`, three test files) — flagged in apply-progress as an explicit follow-up risk, not a defect of this change since task 2.24's scope was two eval scripts only.

### Verdict: **PASS**

All 49 tasks complete and verified on disk, all spec requirements (both delta specs, including the mid-change zero-floor scenario) have passing covering tests, full suite green (3839/3839), ruff and mypy clean, Phase 5 measurement gate closed PASS with recorded evidence, and state.yaml now reflects reality.
