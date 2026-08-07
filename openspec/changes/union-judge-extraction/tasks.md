# Tasks: Union-of-Runs + Selector Judge Replaces the Blind Extraction Cap

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~650-750 (judge.py+test ~220, concept.py+test ~280, config/main/cli tests ~120, evals+template ~60) |
| 400-line budget risk | High |
| Chained PRs recommended | No (single-pr strategy set) |
| Suggested split | single PR, size:exception |
| Delivery strategy | single-pr |
| Chain strategy | size-exception |

Decision needed before apply: Yes
Chained PRs recommended: No
Chain strategy: size-exception
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `extraction/judge.py` leaf module (prompt/parse/validate/select) | PR 1 (single) | `uv run pytest tests/unit/extraction/test_judge.py` | N/A — pure LLM-mocked unit tests, no live harness needed | delete `judge.py` + its test, zero callers yet |
| 2 | `extract_concept_union` orchestrator in `concept.py` + report fields | PR 1 (single) | `uv run pytest tests/unit/extraction/test_concept.py` | N/A — `_SequencedLLM`/`_FakeLLM` fixtures cover it | revert `concept.py` diff; `extract_concept` untouched |
| 3 | Config flag + CLI wiring + stderr notices | PR 1 (single) | `uv run pytest tests/unit/test_config.py tests/unit/cli/test_ingest.py` | `openkos ingest <fixture>` against local Ollama qwen3:8b | flip `DEFAULT_UNION_JUDGE = False`, byte-identical fallback |
| 4 | Pre-archive measurement gate (eval scripts) | PR 1 (single) | `uv run python evals/extraction_cap/run_cap_eval.py` / `uv run python evals/decision_extraction/scripts/run_type_coverage.py` | Requires local Ollama qwen3:8b, live LLM calls | non-blocking for code revert; gate is evidence, not shipped code |

All four units land in one PR per `single-pr` delivery strategy; `size:exception` requires maintainer approval given the ~700-line estimate.

## Phase 1: Judge Module (Foundation)

- [x] 1.1 RED: `tests/unit/extraction/test_judge.py` — `select()` builds messages embedding source text + every candidate title; assert on captured `llm.chat` args (must fail: module doesn't exist yet).
- [x] 1.2 GREEN: Create `src/openkos/extraction/judge.py` — `JudgeCandidate(type, title, description)` dataclass, `_JUDGE_SYSTEM_PROMPT`, `_build_judge_messages()`.
- [x] 1.3 RED: test valid `{"keep": ["<title>", ...]}` reply via `llm/parsing.extract_json_object` returns titles in reply order (mutate: swap `extract_json_object` for `extract_json_items` to see it fail on a bare array).
- [x] 1.4 GREEN: Implement `_validate_selection()` parsing `keep` list into `tuple[str, ...]`.
- [x] 1.5 RED: test non-JSON reply, missing `keep` key, non-list `keep`, non-string elements, empty list — all return `None` (mutate: remove one branch, confirm the matching test fails).
- [x] 1.6 GREEN: Implement the guard branches in `_validate_selection()`.
- [x] 1.7 RED: test `llm.chat` raising any exception inside `select()` returns `None` and nothing propagates (mutate: remove the try/except, confirm test fails).
- [x] 1.8 GREEN: Wrap `select()`'s `llm.chat` call in a single named broad `except Exception` with rationale docstring (D7).
- [x] 1.9 Confirm `judge.py` imports only `llm.base`, `llm.parsing`, stdlib — no import of `concept.py` (static check + mypy).

## Phase 2: Union Orchestrator in concept.py

- [x] 2.1 RED: `tests/unit/extraction/test_concept.py` — add `_SequencedLLM`-driven test: `extract_concept_union` issues 2 identical-message `_extract_once` calls below `_CHUNK_THRESHOLD`; run-2-only candidate survives in the merged union (mutate: hardcode single-call, confirm the new-candidate assertion fails).
- [x] 2.2 GREEN: Implement `extract_concept_union(source_text, *, source_title, llm)` calling `_extract_once` twice for unchunked sources.
- [x] 2.3 RED: test per-run twin-drop happens before merge (run with a title-twin of source; the other run has no twin) — twin absent from union regardless of run order (mutate: twin-drop after merge instead of per-run, confirm test fails).
- [x] 2.4 GREEN: Apply `_drop_source_title_twins` to each run's output before merging.
- [x] 2.5 RED: `_merge_union` collision test — richer body wins; description tie-break; both-equal keeps first-occurrence order (mutate: use first-occurrence-always, confirm richer-body test fails).
- [x] 2.6 GREEN: Implement `_merge_union(key=(type, _normalize_title(title)))` with whole-object swap on richer body/description.
- [x] 2.7 RED: chunked source (`len > _CHUNK_THRESHOLD`) test — exactly `chunks + 1` total LLM calls, `runs == 1` in report, no second extraction pass per chunk (mutate: call `_extract_once` twice per chunk, confirm call-count assertion fails).
- [x] 2.8 GREEN: Branch `extract_concept_union` on `_CHUNK_THRESHOLD`: unchunked = 2 runs + merge; chunked = existing `_dedup_merged` path, judge-only.
- [x] 2.9 RED: >24 merged candidates test — judge sees exactly 24, `pre_judge_dropped` counts the remainder (mutate: pass full list to judge, confirm the judge-input-length assertion fails).
- [x] 2.10 GREEN: Apply `_MAX_JUDGE_CANDIDATES = 24` ceiling before calling `judge.select`, recording `pre_judge_dropped`.
- [x] 2.11 RED: judge success test — `judged_out_titles` names titles the judge dropped; `judge_status == "ok"` (mutate: don't populate `judged_out_titles`, confirm assertion fails).
- [x] 2.12 GREEN: Wire `judge.select()` call, build `JudgeCandidate` list, apply selection.
- [x] 2.13 RED: Procedure re-admission test — a judge-rejected `Procedure` candidate is retained AND absent from `judged_out_titles` (deterministic post-filter, not prompt-driven) (mutate: skip re-admission, confirm candidate missing from output).
- [x] 2.14 GREEN: Implement deterministic re-admission: `kept = [c for c in merged if c.title in selected or c.type == _TWIN_EXEMPT_TYPE]`.
- [x] 2.15 RED: judge failure paths — `llm.chat` raises `OllamaError`, empty reply, unparseable reply — all three: full merged union kept, `judge_status == "failed"`, no exception escapes (mutate: let one failure mode propagate, confirm test fails).
- [x] 2.16 GREEN: Catch `judge.select()` returning `None`, fall back to full backstop-truncated union, set `judge_status = "failed"`.
- [x] 2.17 RED: backstop test — judge-selected set of 7 passes through unchanged; set of >12 (or failure-degraded) truncated to exactly 12, applied strictly after re-admission (mutate: apply backstop before judge, confirm order-dependent test fails).
- [x] 2.18 GREEN: Apply `_UNION_BACKSTOP = 12` once, last, after re-admission/failure-degrade.
- [x] 2.19 RED: extraction-run-2 raising an exception (not judge) still propagates unswallowed (mutate: wrap run 2 in try/except, confirm exception-propagation test fails).
- [x] 2.20 GREEN: Confirm/leave `_extract_once` calls outside any broad catch in the union path.
- [x] 2.21 GREEN: Add `ExtractionReport` fields: `runs: int = 1`, `judge_status: str = "skipped"`, `judged_out_titles: tuple[str, ...] = ()`, `pre_judge_dropped: int = 0`; keep `produced`/`retained`/`discarded_titles` semantics tied to the final cap only.
- [x] 2.22 Regression check: existing `extract_concept` test suite green untouched; `_SYSTEM_PROMPT` byte-identical (diff check).
- [x] 2.23 RED (gate finding, 2026-08-07): valid judge selection admitting ZERO objects while the merged union is non-empty MUST degrade to the backstop-truncated union with a distinct `judge_status` and count as a degrade for `_judge_failure_notice` — never return `[]` with `judge_status="ok"`. Reproduced on `TS3005a.transcript`: both runs collapse to one umbrella Event, judge rejects it, pipeline returned zero objects. Test: `_SequencedLLM` with judge reply keeping only a fabricated title (admitted set empty after closed-set matching, no Procedure to re-admit) → assert output equals backstop-truncated union and status is the degrade value (mutate: return the empty admitted set as-is, confirm the non-empty assertion fails).
- [x] 2.24 GREEN: implement the empty-admission floor in `extract_concept_union` after Procedure re-admission and before the backstop; also correct the hallucinated `#458` references to `#456` in `evals/extraction_cap/run_cap_eval.py` and `evals/decision_extraction/scripts/run_type_coverage.py` help/docstrings.

## Phase 3: Config Flag and CLI Wiring

- [x] 3.1 RED: `tests/unit/test_config.py` — flag absent defaults to `True`; `union_judge: false` in YAML survives (`is not None` check, not truthiness); non-bool value raises a guarded config error (mutate: use truthiness instead of `is not None`, confirm the `false`-survives test fails).
- [x] 3.2 GREEN: Add `DEFAULT_UNION_JUDGE = True` and typed `union_judge: bool` field to `src/openkos/config.py` with `is not None` + isinstance guard.
- [x] 3.3 GREEN: Add `union_judge` key (commented, default true) to `openkos.yaml.template`.
- [x] 3.4 RED: `tests/unit/cli/test_ingest.py` — `union_judge=False` kwarg on `_stage_derived_objects` calls `extract_concept` exactly once per source, no judge call (mutate: always call union path, confirm call-count test fails).
- [x] 3.5 GREEN: Add `union_judge: bool = False` kwarg to `_stage_derived_objects` in `src/openkos/cli/main.py`; CLI call site injects `cfg.union_judge`.
- [x] 3.6 RED: judge-failure integration test — fake LLM backend where base extraction succeeds but judge call raises `OllamaError`: merged-union candidates (backstop-truncated) are staged/written, a `_judge_failure_notice` distinct from `_judge_selection_notice`/`_extraction_cap_notice` appears on stderr, exit code 0 (mutate: reuse `_extraction_cap_notice` text, confirm distinct-notice assertion fails).
- [x] 3.7 GREEN: Implement `_judge_failure_notice()` and `_judge_selection_notice()` in `main.py`, wired to `ExtractionReport.judge_status`.
- [x] 3.8 RED: full-LLM-unavailability test — `chat` raises during the base extraction call itself (not judge): behavior unchanged from existing Source-only degrade (mutate: route this through the judge-failure path instead, confirm Source-only assertion fails).
- [x] 3.9 GREEN: Confirm base-extraction failures still propagate to the existing Source-only fallback path, untouched.
- [x] 3.10 RED: backstop-of-12 staging test — union+judge selection yielding >12 valid objects writes no more than 12 derived files (mutate: use old cap of 6, confirm count assertion fails).
- [x] 3.11 GREEN: Confirm `_stage_derived_objects` consumes the already-backstopped 12-object set from `extract_concept_union`, same slug/collision rules as today, no chunk-specific exception.

## Phase 4: Eval Harness Updates

- [x] 4.1 Update `evals/extraction_cap/run_cap_eval.py` to run both the single-cap path and union+judge path per fixture, reporting recall deltas.
- [x] 4.2 Confirm `evals/decision_extraction/scripts/run_type_coverage.py` (AMI harness) accepts a `union_judge` toggle for TS3005b before/after comparison.

## Phase 5: Pre-Archive Measurement Gate (requires local Ollama qwen3:8b)

- [x] 5.1 Run `uv run python evals/extraction_cap/run_cap_eval.py` on the 3 adjudicated fixtures with union+judge ON; compare post-cap recall against today's baselines (0.71 / 0.88 / 0.73) — MUST NOT regress on any fixture.
- [x] 5.2 Run `uv run python evals/decision_extraction/scripts/run_type_coverage.py` (AMI harness) 3x on TS3005b; compare type-coverage against today's baseline (6, 6, 6-of-10) — MUST NOT regress.
- [x] 5.3 Record before/after results in `evals/extraction_cap/report.md` and `evals/decision_extraction/report.md`; block archive if any fixture regresses per the spec's Pre-Archive Measurement Gate requirement.
