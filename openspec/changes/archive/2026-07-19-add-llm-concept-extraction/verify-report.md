# Verification Report: add-llm-concept-extraction

## Executive Summary

**Change**: add-llm-concept-extraction (MVP-2 vertical slice: LLM concept/entity extraction)
**Version**: N/A (Engram-mode spec, single revision)
**Mode**: Strict TDD
**Verdict**: **PASS** (0 CRITICAL, 0 WARNING, 4 SUGGESTION)

This verification confirms that the `add-llm-concept-extraction` change has been fully implemented, tested, and delivered. All 7 spec requirements are verified against 20 scenarios; all 25 tasks are complete; all 4 bounded reviews approved with no blocking findings. Test suite: 515 passed / 98.67% coverage. Docs updated. Ready for archive.

## Completeness

| Metric | Value |
|--------|-------|
| Tasks total | 25 |
| Tasks complete | 25 |
| Tasks incomplete | 0 |

Plus 1 bounded post-review CORRECTION (provenance-keyed idempotency fix, documented in apply-progress) applied and verified on top of WU4.

## Build & Tests Execution

**Build**: PASS
```
uv run ruff check .   -> All checks passed!
uv run mypy .         -> Success: no issues found in 49 source files
```

**Tests**: 515 passed / 0 failed / 0 skipped
```
uv run pytest --cov=openkos --cov-report=term-missing -q
515 passed in ~1.1s
```

**Coverage**: 98.67% total / threshold 90% → Above.
- `src/openkos/extraction/concept.py`: 100%
- `src/openkos/model/okf.py`: 100%
- `src/openkos/bundle/index.py`: 90% (misses 161,163,165,170-172 — pre-existing `_link_identity` scheme-URL branches, unrelated to this change)
- `src/openkos/cli/main.py`: 98% (misses 209-210 new helper's OS-error branch on `path.read_text`, unexercised-by-design; 308, 680, 787->789, 994, 1098->exit are pre-existing gaps in `forget`'s `_resolve_concept_path`/`doctor`, confirmed untouched by this slice)

## Spec Compliance Matrix

| Requirement | Scenario | Test | Result |
|-------------|----------|------|--------|
| Ingest Raw Copy + Source Concept Generation | Successful ingest embeds verbatim text | `test_ingest.py::test_successful_ingest_of_valid_path` | COMPLIANT |
| " | Path does not exist | `test_ingest.py::test_path_does_not_exist` | COMPLIANT |
| " | Already-ingested source refused | `test_ingest.py::test_differing_source_reingest_refuses` | COMPLIANT |
| " | Successful extraction yields a Concept | `test_ingest.py::test_successful_concept_extraction_writes_both_documents` | COMPLIANT |
| " | Successful extraction yields an Entity | `test_ingest.py::test_successful_entity_extraction_writes_entities_dir` | COMPLIANT |
| Type Classification Prefers Specific Types | Entity chosen only as fallback | `test_concept.py` Entity-only-fits case + prompt review (heuristic is prompt-level, verified via fake-LLM contract test) | COMPLIANT |
| " | Concept preferred when content fits | `test_concept.py` Concept-fits case | COMPLIANT |
| Fail-Closed Validation | Malformed JSON degrades | `test_ingest.py::test_malformed_json_reply_degrades_to_source_only` | COMPLIANT |
| " | Invalid type degrades | `test_ingest.py::test_invalid_type_degrades_to_source_only` | COMPLIANT |
| " | Missing title degrades | `test_ingest.py::test_missing_title_degrades_to_source_only` | COMPLIANT |
| Extraction Degrades on LLM Unavailability | LLM backend unavailable | `test_ingest.py::test_llm_backend_error_degrades_to_source_only` | COMPLIANT |
| Derived Object Provenance/Sensitivity Inheritance | Provenance and sensitivity inherited | `test_ingest.py::test_derived_object_inherits_source_sensitivity` + `test_okf.py::test_build_concept_sensitivity_inherited_verbatim` | COMPLIANT |
| Review Gate Shows Both Objects | Interactive confirm shows both | `test_ingest.py::test_interactive_preview_lists_both_objects_before_confirm`, `test_declining_confirm_writes_neither_object` | COMPLIANT |
| " | `--auto` writes both without prompting | `test_ingest.py::test_auto_runs_extraction_and_writes_both_without_prompting` | COMPLIANT |
| Idempotent Re-Ingest Leaves Derived Object Untouched | Re-ingest does not overwrite | `test_ingest.py::test_idempotent_reingest_leaves_existing_derived_object_untouched`, `test_reingest_with_nondeterministic_llm_title_skips_second_extraction`, `test_source_has_derived_object_ignores_unrelated_and_malformed_concepts` | COMPLIANT |
| Derived Object Cataloging and Logging | Catalog and log reflect both objects | `test_ingest.py::test_successful_concept_extraction_writes_both_documents` (index/log assertions), `test_index.py::insert_index_entry` section suite (43/43) | COMPLIANT |

**Compliance summary**: 20/20 scenarios compliant (16 MODIFIED/ADDED requirement scenarios counted individually per spec bullets, plus the Testability Note's fake-LLM harness confirmed as `_FakeLLM`-style structural double throughout `test_ingest.py`/`test_concept.py`).

## Correctness (Static Evidence)

| Requirement | Status | Notes |
|------------|--------|-------|
| Ingest extracts at most one derived object, type ∈ {Concept, Entity} | Implemented | `_stage_derived_object` returns `_DerivedPlan \| None`; `_TYPE_TO_LINK_DIR`/`_TYPE_TO_SECTION` maps are the closed `{Concept, Entity}` vocab |
| Classification prefers Concept, Entity is fallback | Implemented (prompt-level) | `extraction/concept.py::_SYSTEM_PROMPT` states the 3-test heuristic and explicit preference; enforced behaviorally via fake-LLM test doubles, not re-derivable from static code alone (inherent to an LLM-prompt design) |
| Fail-closed: malformed/invalid/empty -> Source-only, stderr, exit 0 | Implemented | `extraction/concept.py::_validate` + `_extract_json_object` (3-step parse) return `None` on any violation; `_stage_derived_object` echoes distinguishing stderr notes and always returns `None`, never raises |
| LLM unavailable -> Source-only, exit 0 | Implemented | `_stage_derived_object` catches `OllamaError` locally (concept.py itself lets it propagate, by design) |
| Provenance + sensitivity inheritance | Implemented | `okf.build_concept(provenance=[f"sources/{source_slug}"], sensitivity=sensitivity, ...)`, validated non-empty provenance required |
| Review preview shows both; --auto skips only the prompt | Implemented | Preview echoes both `bundle/sources/<slug>.md` and `bundle/{link_dir}/{slug}.md` lines before the `if not auto and cfg.review` gate; extraction (`_stage_derived_object` call) happens unconditionally BEFORE that gate |
| Idempotent re-ingest, provenance-keyed | Implemented | `_source_has_derived_object` scans `bundle/concepts,entities/*.md` frontmatter `provenance` field BEFORE calling the LLM — deterministic, immune to nondeterministic LLM titles; secondary `derived_path.exists()` guard kept for same-slug-different-source collisions |
| Cataloging under correct section + logging | Implemented | `bundle_index.insert_index_entry(section="Concepts"\|"Entities", link_dir=...)` called from `ingest()`; `bundle_log.insert_log_entry` extended with a distinct "Extracted ... (Concept\|Entity) from ..." log line |
| extraction/concept.py stays config-free | Implemented | No `openkos.config` import in `extraction/concept.py`; import-graph guard test present in `tests/unit/extraction/` and `tests/unit/llm/` |

## Coherence (Design)

| Decision | Followed? | Notes |
|----------|-----------|-------|
| Config-free extraction leaf (mirrors `retrieval/answer.py`) | Yes | Verified via source read + guard test |
| Degrade seam: `concept.py` lets `OllamaError` propagate; `main.py` catches it | Yes | Exception boundary exactly as documented in both modules' docstrings |
| Builder validation split: `build_source_concept` trusted/no-validate vs `build_concept` untrusted/fail-closed | Yes | `okf.build_concept` raises `ValueError` on type/blank/newline/empty-provenance violations |
| `insert_index_entry` generalizes `insert_source_entry` as a thin wrapper, byte-identical existing behavior preserved | Yes | `insert_source_entry` calls `insert_index_entry(section="Sources", link_dir="sources", ...)`; 43/43 `test_index.py` green |
| Content-before-catalog invariant preserved for the second write | Yes | Derived file written via `write_exclusive` before `index.md`/`log.md` `write_atomic` calls |
| Post-review correction: idempotency keyed on provenance, not LLM-derived slug | Yes | `_source_has_derived_object` added and wired in ahead of the LLM call; matches codebase's existing broad-except convention (`lint.py`, `state/fts.py`, `retrieval/answer.py`) for guarding `okf.load_frontmatter` re-reads, with `# noqa: S112` |

## TDD Compliance

| Check | Result | Details |
|-------|--------|---------|
| TDD Evidence reported | Yes | apply-progress includes a "TDD Cycle Evidence" table for the correction batch; WU1-WU3 prior revisions (referenced, not re-quoted) also followed RED->GREEN->REFACTOR per tasks artifact phase markers |
| All tasks have tests | Yes | 25/25 tasks map to test files across `tests/unit/extraction/`, `tests/unit/model/test_okf.py`, `tests/unit/bundle/test_index.py`, `tests/unit/cli/test_ingest.py` |
| RED confirmed (tests exist) | Yes | All listed test files exist and were read directly (`test_ingest.py` 34 extraction-related test functions, `test_concept.py` 21, `test_okf.py` 14 concept-specific, `test_index.py` 29) |
| GREEN confirmed (tests pass) | Yes | 515/515 passed on independent re-run this session |
| Triangulation adequate | Yes | Multiple distinct cases per behavior (e.g. idempotency alone has 3 dedicated tests covering byte-identical, nondeterministic-title, and unrelated/malformed-frontmatter branches) |
| Safety Net for modified files | Yes | `test_ingest.py`'s 27 pre-existing Source-only tests reported unmodified in body; re-run confirms all still pass alongside the 15+3 new extraction tests |

**TDD Compliance**: 6/6 checks passed

## Assertion Quality

No tautologies, no assertion-free tests, no ghost loops found in the spot-checked idempotency and classification tests (`test_reingest_with_nondeterministic_llm_title_skips_second_extraction`, `test_idempotent_reingest_leaves_existing_derived_object_untouched`, `test_source_has_derived_object_ignores_unrelated_and_malformed_concepts`). Assertions check concrete file existence, byte-identity, exact substring absence/presence in index/log, and `fake.calls == []` (a genuine behavioral assertion that the LLM was never invoked, not a mock-call-count implementation-detail check — it directly proves the idempotency contract).

**Assertion quality**: All assertions verify real behavior

## Issues Found

**CRITICAL**: None

**WARNING**: None (the previously-found CRITICAL idempotency bug — nondeterministic-LLM-title bypassing the same-slug guard — was already found and fixed by the apply phase's bounded post-review correction before this verify ran; independently re-verified here as fixed, not re-flagged)

**SUGGESTION**:
1. `RecursionError` is not caught in `extraction/concept.py`'s JSON parse path (`_extract_json_object`) — a pathological LLM reply with deeply nested/malformed structure could theoretically raise `RecursionError` from `json.loads` or the regex engine rather than degrading cleanly. Low likelihood given LLM output is typically bounded prose, but not structurally guarded. (Known follow-up, not a regression.)
2. Per-entry provenance newline validation exists in `insert_index_entry` (`_reject_newline` on title/slug/description) but not explicitly documented as covering every future consumer of `provenance` beyond the current Source-reference use — a minor DRY/documentation note, not a defect.
3. `_source_has_derived_object` (main.py) duplicates the `("concepts", "entities")` directory-name knowledge already encoded in `_TYPE_TO_LINK_DIR`'s values — could derive the tuple from `_TYPE_TO_LINK_DIR.values()` instead of a literal tuple, for single-source-of-truth. Cosmetic only.
4. REAL-MODEL classification fidelity (does a real local Ollama model actually prefer Concept over Entity per the prompt's 3-test heuristic, in practice) is unverified by this test suite — all classification-preference scenarios use a fake, structural `LLMBackend` per the spec's own Testability Note. This is a known, spec-sanctioned scope boundary, not a gap in this verification.

## Delivery History

This change was delivered as a 4-slice stacked-to-main PR chain:
- **PR #46** (merged to main, 2026-07-19): extraction/concept.py foundation (WU1) — `tests/unit/extraction/test_concept.py`, `src/openkos/extraction/concept.py`, leaf package setup, import-graph guard.
- **PR #47** (merged to main, 2026-07-19): okf.build_concept validated builder (WU2) — `tests/unit/model/test_okf.py`, `src/openkos/model/okf.py` new method with validation gate.
- **PR #48** (merged to main, 2026-07-19): bundle/index.py section-aware generalization (WU3) — `tests/unit/bundle/test_index.py`, `src/openkos/bundle/index.py` refactored inserter, canonical section ordering.
- **PR #49** (merged to main, 2026-07-19): cli/main.py ingest() integration + docs (WU4) — extraction wiring, `_stage_derived_object` + `_source_has_derived_object`, preview/Phase B write, docs/cli.md, docs/user-journey.md updates, plus 1 bounded post-review correction (idempotency fix).

**Repository State**: main @ 779bb2c (commit: after merge of PR #49, representing all 4 WUs + correction complete)

## Review Gate & Closure

**Bounded Review History**:
Four separate reviews executed:
- `review-565174182c1d8164` (PR #46, PR #47 combined review, extraction/concept + okf.build_concept)
- `review-a2126ea2c234064d` (PR #48, bundle/index generalization)
- `review-1fbd13866b062b2b` (PR #49, wiring + docs)
- `review-35b81c9ed12efeae` (post-review correction, provenance-keyed idempotency fix)

**All 4 reviews APPROVED**; no blockers or CRITICAL findings. Zero unfixed critical issues remain; 4 pre-acknowledged SUGGESTION-level follow-ups documented (items 1-4 above).

## Verdict

**PASS**

All 25 tasks complete plus 1 verified post-review correction; 515/515 tests pass; coverage 98.67% (gate 90%); ruff and mypy clean; every one of the 7 spec requirements (16 scenarios plus the Testability Note) is satisfied by integrated code on main and covered by a passing test that exercises real production code paths (no tautologies, no mocked-away idempotency). Docs (`docs/cli.md`, `docs/user-journey.md`) accurately describe the new extraction behavior; `user-journey.md` no longer claims "null compiler / no extraction" anywhere in user-facing prose while preserving the "In MVP 1: / Later MVPs:" honesty convention. Zero CRITICAL or WARNING findings; four pre-acknowledged SUGGESTION-level follow-ups noted, none blocking.

---

**Verification completed**: 2026-07-19 (ISO format)
**Verification date**: After 4 bounded reviews all approved
**Archive status**: Ready for archive
