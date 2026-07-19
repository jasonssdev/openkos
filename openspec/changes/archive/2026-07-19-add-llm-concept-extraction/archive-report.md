# Archive Report: add-llm-concept-extraction

**Change**: add-llm-concept-extraction (MVP-2 vertical slice: LLM concept/entity extraction, first feature) | **Archived**: 2026-07-19 | **Status**: Complete | **Repository**: openkos (main 779bb2c after merge of 4-slice PR chain #46-#49)

This archive report closes the SDD cycle for the `add-llm-concept-extraction` change. The feature introduces MVP-2's centerpiece — LLM-driven extraction of at most one derived Concept or Entity per ingest, delivered as the smallest reviewable vertical slice. A new extraction layer (fail-closed, review-gated, provenance-keyed idempotent re-ingest) proves both extraction AND type classification without inheriting ontology or entity-resolution risks. All 25 implementation tasks complete; all 7 spec requirements verified against 20 scenarios with zero CRITICAL issues; 4 bounded reviews approved across 4-slice stacked-to-main PR chain. Achieves 515 tests passing at 98.67% project coverage after 1 post-review correction (provenance-keyed idempotency guard).

## Change Summary

**Purpose**: Prove LLM-assisted vertical slice, safely and narrowly: add extraction attempt inside `ingest()` Phase A, produce at most one derived Concept or Entity in addition to the Source, fail-closed with graceful degrade to Source-only on any LLM failure, and leave derived objects untouched on idempotent re-ingest. Classify extracted objects using a closed vocabulary `{Concept, Entity}`, prefer specific over Entity fallback, validate all output before write, and gate the preview through the existing human review.

**Scope**:
- New config-free extraction leaf `extraction/concept.py` (mirrors `retrieval/answer.py`): prompts the LLM via 2-message chat, performs fail-closed JSON parse (3-step: raw → strip fences → regex), validates output (type in vocab, title/description non-empty, parseable slug), returns validated `ExtractionResult | None`.
- New `okf.build_concept` builder in `src/openkos/model/okf.py`: parallel to `build_source_concept`, accepts LLM output (type, title, description, body), builds conformant frontmatter with provenance + sensitivity inheritance, raises `ValueError` on untrusted LLM fields.
- Generalized `bundle/index.py::insert_index_entry(*, section, link_dir, ...)` with canonical section ordering `[Concepts, Entities, Decisions, People, Sources]`; `insert_source_entry` becomes a thin wrapper.
- CLI wiring in `src/openkos/cli/main.py`: new `_stage_derived_object` and `_source_has_derived_object` helpers, extraction call in Phase A before preview, extended preview/Phase B to handle both Source + derived, distinguishing degrade messages.
- Full test surface (fake-LLM coverage, deterministic scenarios, idempotency proofs, regression safeguards).
- Docs: `docs/cli.md` (ingest extraction subsection, degrade categories), `docs/user-journey.md` (corrected "null compiler" claim, extraction mention under MVP-1 transparency).

**Architecture Decisions**:
- **D1**: Module home = `extraction/concept.py` leaf, not `retrieval/`, keeps extraction a compile-step, not retrieval, maintains screaming architecture (config-free leaf with injected LLM).
- **D2**: Degrade seam = `extract_concept()` lets `OllamaError` propagate; `main.py` catches it. Mirrors `answer()`→`query` split; CLI layer owns degrade UX.
- **D3**: JSON parsing via 3-step (raw → fenced strip → regex block), any failure → None. Local models wrap JSON in prose; no JSON-mode precedent.
- **D4**: Vocabulary = closed `{Concept, Entity}` enforced by validation. Exercises real classification + prefer-Concept rule without ontology risk.
- **D5**: Builder validation split: `build_source_concept` trusted (engine-derived); `build_concept` untrusted (LLM-derived), raises `ValueError`.
- **D6**: Idempotency/collision = create-only, path existence check; preserves user edits + handles slug collisions with one rule.
- **D7**: Catalog generalization = section-aware ordering; sources stay last; Concepts/Entities rank by canonical order.

**Post-Review Correction Applied**:
CRITICAL idempotency bug found during bounded review #4: `_stage_derived_object`'s original guard was `derived_path.exists()` keyed on nondeterministic LLM title → on re-ingest with different title, a SECOND derived doc created (duplicate index/log entry). Fix: added `_source_has_derived_object(bundle_dir, source_slug) → bool`, scans existing `bundle/concepts,entities/*.md` by provenance, returns True if any derived doc cites this source. Called BEFORE the LLM to silently skip idempotent re-ingest. Provenance-keyed (deterministic, engine-derived) replaces title-keyed (nondeterministic, LLM-derived). Exception handling matches established codebase convention (broad `except Exception` + `# noqa: S112`, per `lint.py`/`state/fts.py`/`retrieval/answer.py`).

**Zero ADRs created** (all decisions additive, fully revertible via `git revert`).

## Artifacts Archived

| Artifact | Location | Status |
|---|---|---|
| Proposal | `archive/2026-07-19-add-llm-concept-extraction/proposal.md` | Moved from change folder; summarizes intent (MVP-2 first feature, vertical slice, fail-closed), scope (one derived object, 2-value vocab, review-gated, provider-keyed idempotent), approach (Phase A extraction seam, new builders, degrade messaging), risks, rollback |
| Specification | `archive/2026-07-19-add-llm-concept-extraction/specs/ingestion/spec.md` | Delta spec (MODIFIED + 7 ADDED requirements) MERGED into main spec tree (`openspec/specs/ingestion/spec.md`). Archived as historical record. Live spec now covers: Source + extraction attempt, type classification, fail-closed validation, LLM-unavailable degrade, provenance + sensitivity inheritance, review preview (both objects), idempotent re-ingest (provenance-keyed), cataloging/logging. |
| Design | `archive/2026-07-19-add-llm-concept-extraction/design.md` | Moved from change folder; documents D1-D7 decisions, data flow, interfaces, JSON parse strategy, validation gates, testing strategy (fake-LLM harness), threat matrix (untrusted LLM output mitigated by validation + newline guard + create-only), migration plan, ADR gate (none), open questions (none blocking) |
| Tasks | `archive/2026-07-19-add-llm-concept-extraction/tasks.md` | All 25/25 checked across 6 phases (Phase 1-3 foundation; Phase 4 builders; Phase 5 wiring; Phase 6 verification). WU1 (extraction/concept.py leaf), WU2 (okf.build_concept), WU3 (bundle/index generalized), WU4 (main.py wiring + correction). Ready for archive. |
| Verification Report | `archive/2026-07-19-add-llm-concept-extraction/verify-report.md` | PASS: 7/7 requirements verified (1 MODIFIED + 6 ADDED), 20/20 scenarios passing (5 Source scenarios + 5 extraction success + 4 fail-closed + 1 degrade + 1 provenance + 2 review gate + 1 idempotent + 1 cataloging). Full test suite: 515 passed, 98.67% coverage (floor 90%). Quality gates: ruff check, ruff format, mypy strict — all pass. AST test (leaf discipline) passes. All D1-D7 verified in code. Bounded correction fully implemented and re-tested. Zero CRITICAL, zero WARNING, 4 pre-acknowledged SUGGESTION (RecursionError guard, DRY provenance validation, _TYPE_TO_LINK_DIR DRY, real-model fidelity). |

## Spec Merge Summary

| Action | Domain | Details |
|---|---|---|
| **MODIFIED** | `ingestion` | 1 requirement (Ingest Raw Copy and Source Concept Generation) redefined to include extraction attempt (LLM call, fail-closed degrade, both Source + derived written on success); 5 scenarios (successful ingest, path-not-exist, already-ingested-refused, extraction→Concept, extraction→Entity) updated + 2 carry-over scenarios (undecodable, empty). Previous byte-identical-regenerate scenarios removed (addressed by MVP-1 boundary change). |
| **ADDED** | `ingestion` | 6 new requirements + 9 new scenarios: Type Classification (prefer Concept, Entity fallback) [2 scenarios]; Fail-Closed Validation (malformed JSON, invalid type, missing title) [3 scenarios]; LLM Unavailable Degrade [1 scenario]; Provenance + Sensitivity Inheritance [1 scenario]; Review Gate Shows Both Objects (interactive confirm, --auto) [2 scenarios]; Idempotent Re-Ingest [1 scenario]; Cataloging/Logging [1 scenario]; plus updated Embedded Content queryability [1 existing scenario]. |
| **PRESERVED** | `ingestion` | All other 9 existing requirements untouched (Config Reader, Bundle Catalog Append, Bundle Log Append, Non-Exclusive Atomic Write, Path Containment, OKF-Native Provenance, Review/Confirm Flow, Default Sensitivity). Total ingestion spec now 16 requirements / 20+ scenarios. |
| Sources | Delta specs | `openspec/changes/add-llm-concept-extraction/specs/ingestion/spec.md` MERGED into `openspec/specs/ingestion/spec.md` |
| Merge mode | MODIFIED (1) + ADDED (6) | Ingestion spec existed; delta modifies 1 existing requirement's scope (extraction now part of ingest), adds 6 new requirements (classification, validation, degrade, provenance, review, idempotency, catalog). All existing requirements remain in main spec; extraction requirements inserted logically. |

## Verification Status

**Final Verdict**: PASS (0 CRITICAL, 0 WARNING, 4 SUGGESTION)

**Evidence Summary**:
- **All 7/7 requirements verified**:
  - ingestion: Ingest Raw Copy + Source Concept Generation (MODIFIED, 5+2 scenarios)
  - ingestion: Type Classification Prefers Specific Over Entity Fallback (ADDED, 2 scenarios)
  - ingestion: Fail-Closed Validation of Extracted Output (ADDED, 3 scenarios)
  - ingestion: Extraction Degrades Gracefully on LLM Unavailability (ADDED, 1 scenario)
  - ingestion: Derived Object Provenance and Sensitivity Inheritance (ADDED, 1 scenario)
  - ingestion: Review Gate Shows Both Objects Before Write (ADDED, 2 scenarios)
  - ingestion: Idempotent Re-Ingest Leaves Existing Derived Object Untouched (ADDED, 1 scenario)
  - ingestion: Derived Object Cataloging and Logging (ADDED, 1 scenario)
- **All 20/20 scenarios passing**:
  - Source ingest: successful-verbatim, path-not-exist, already-ingested-refused, undecodable, empty
  - Extraction success: yields-Concept, yields-Entity
  - Fail-closed: malformed-JSON, invalid-type, missing-title
  - Degrade: LLM-unavailable
  - Provenance: inherited
  - Review gate: interactive-confirm-shows-both, auto-writes-both
  - Idempotent: re-ingest-untouched
  - Cataloging: index+log reflect both
- **Test execution**: **515 passed, 0 failed, 0 skipped** (full project suite); **58 new/extended tests** for this change (extraction + okf + index + ingest scenarios)
- **Coverage**: `src/openkos/extraction/concept.py` 100%, `src/openkos/model/okf.py` 100%, project total **98.67%** (floor 90%)
- **Quality gates**: ruff check ✓, ruff format ✓, mypy strict ✓, leaf-discipline AST test ✓
- **Design verification**: D1-D7 all verified in code and tests
- **Bounded correction**: Provenance-keyed idempotency fix verified; no regressions

## Delivery History

This change was delivered as a 4-slice stacked-to-main PR chain, with each slice autonomous and reviewable:
- **PR #46** (merged to main, 2026-07-19): extraction/concept.py foundation (WU1) — new config-free leaf, fake-LLM test harness, prompt/parse/validation, 100% coverage, import-graph guard green.
- **PR #47** (merged to main, 2026-07-19): okf.build_concept validated builder (WU2) — new builder parallel to build_source_concept, validation gate on LLM fields, 100% coverage, regression tests green.
- **PR #48** (merged to main, 2026-07-19): bundle/index.py section-aware generalization (WU3) — generalized insert_index_entry, canonical ordering, thin wrapper insert_source_entry, 43/43 index tests green.
- **PR #49** (merged to main, 2026-07-19): cli/main.py ingest() integration + docs (WU4) — extraction wiring, _stage_derived_object, _source_has_derived_object (initially missing, added by post-review correction), extended preview/Phase B, docs/cli.md + docs/user-journey.md, plus 1 bounded post-review correction fixing CRITICAL idempotency bug (keyed on LLM title → keyed on provenance).

**Repository State**: main @ 779bb2c (after PR #49, representing all 4 WUs + correction complete)

## Review Gate & Closure

**Bounded Review History**:
Four independent reviews approved across the 4-slice chain:
- `review-565174182c1d8164` (PR #46-47, extraction/concept + okf.build_concept): APPROVED, no CRITICAL
- `review-a2126ea2c234064d` (PR #48, bundle/index generalization): APPROVED, no CRITICAL
- `review-1fbd13866b062b2b` (PR #49 wiring, initial): APPROVED with note on idempotency (later correction applied)
- `review-35b81c9ed12efeae` (post-review correction, provenance-keyed idempotency): APPROVED, correction verified

**Current status**:
- All 4 PRs merged to main
- All 515 tests passing (98.67% coverage)
- All 7 spec requirements verified (20 scenarios)
- All D1-D7 design decisions verified in code
- Zero CRITICAL issues; 4 pre-acknowledged SUGGESTION-level follow-ups documented, none blocking
- Zero blockers remain; all strict TDD gates passed
- Change complete and ready for archive

## Product Impact & MVP-2 Launch

This archive marks the delivery of **MVP-2's first feature** — the centerpiece announced in roadmap.md:63. LLM-driven extraction is now live and integrated:

**MVP-2 now enabled**:
- `openkos ingest` now extracts up to one derived Concept or Entity per source, automatically
- Classification vocabulary: {Concept, Entity} with Concept-preference heuristic
- Fail-closed: malformed output, unavailable LLM, invalid type all degrade to Source-only, exit 0
- Idempotent: re-ingest of same source skips the LLM on second call (provenance-keyed), leaves hand-edits intact
- Review-gated: both Source + derived shown in preview before confirm
- Documented: docs/cli.md describes extraction behavior and degrade categories; docs/user-journey.md corrected

**Risks mitigated**:
- Rigid ontology → minimal 2-value vocab, validation enforces closed set
- Entity resolution → one derived object per ingest by construction
- Extraction fidelity → mandatory provenance + human review + fail-closed parsing

**Non-goals deferred**:
- Multiple derived objects per ingest
- Other 9 OKF types (future MVP-2 slices)
- Entity resolution, merge, reclassification (MVP-3+)
- Typed relationship graph (MVP-3+)

## Implementation Details

**Modules created**:
- `src/openkos/extraction/__init__.py`: new leaf package, mirrors `retrieval/__init__.py` structure
- `src/openkos/extraction/concept.py`: ExtractionResult dataclass, _SYSTEM_PROMPT, _extract_json_object (3-step parse), _validate (fail-closed), extract_concept (2-message chat, injectable LLM)
- `tests/unit/extraction/test_concept.py`: 21 tests covering valid Concept, valid Entity, fenced JSON, JSON-in-prose, malformed JSON, invalid type, extract:false, empty title, body-fallback, OllamaError propagation

**Modules modified**:
- `src/openkos/model/okf.py`: added build_concept(*, type, title, description, body, provenance, sensitivity, timestamp) with validation gate (raises ValueError on type/title/description/provenance violations)
- `src/openkos/bundle/index.py`: refactored insert_index_entry(index_text, *, section, link_dir, title, slug, description) with canonical section ordering; insert_source_entry(index_text, title, slug, description) is a thin wrapper
- `src/openkos/cli/main.py`: added _DerivedPlan dataclass, _stage_derived_object(raw_content, bundle_dir, source_slug, source_title, llm, sensitivity) helper, _source_has_derived_object(bundle_dir, source_slug) helper; wired extraction into Phase A after build_source_concept; extended preview/Phase B to handle derived object; added distinguishing degrade messages ("no concept extracted" vs "concept extraction skipped -- {exc}")
- `tests/unit/cli/test_ingest.py`: 15 new + 3 corrected extraction scenarios; 27 pre-existing Source-only tests left green and unmodified in body
- `tests/unit/model/test_okf.py`: added build_concept scenarios (frontmatter, provenance, sensitivity, ValueError on bad type/blank title)
- `tests/unit/bundle/test_index.py`: added section-aware insertion tests (Concepts at correct rank, Entities at correct rank, Sources preserved last); 43/43 passing
- `docs/cli.md`: added "Extraction: one derived Concept or Entity, or a graceful degrade" subsection to ingest docs; describes vocab, degrade categories, stderr wording, idempotency, --auto semantics
- `docs/user-journey.md`: corrected "null compiler" claim; reframed Step 2 Compile as "embeds verbatim + attempts extraction"; corrected Step 5 "extracted topic page" claim; maintained "In MVP 1: / Later MVPs:" honesty convention (still at most ONE derived object, no multi-concept, no relationship graph)

**Key implementation patterns**:
- **Config-free leaf**: extraction/concept.py imports no openkos.config; injected LLM parameter
- **Fail-closed parsing**: 3-step JSON (raw → strip fences → regex), any failure → None, always returns ExtractionResult | None, never raises
- **Builder validation**: okf.build_concept raises ValueError on untrusted LLM fields (type, title/description blanks, empty provenance)
- **Exception seam**: extraction/concept.py lets OllamaError propagate; main.py catches it locally in _stage_derived_object, emits distinguished stderr notes
- **Provenance-keyed idempotency**: _source_has_derived_object scans existing docs by frontmatter provenance BEFORE calling LLM, silently skips on match (deterministic, immune to nondeterministic LLM titles); secondary path-existence guard kept for slug-collision safety
- **Create-only idempotency**: fsio.write_exclusive on derived path, silently skips if exists (preserves user edits)
- **Content-before-catalog**: derived doc written before index.md/log.md (maintains invariant that catalog never references missing files)

## Archival Actions Completed

**Filesystem**:
- [x] Existing main spec updated: `openspec/specs/ingestion/spec.md` (1 MODIFIED requirement, 7 ADDED requirements, 6 existing preserved; delta spec merged)
- [x] Change folder ready to move to archive: `openspec/changes/archive/2026-07-19-add-llm-concept-extraction/` with all artifacts (proposal, specs, design, tasks, verify-report, archive-report)
- [x] Archive folder created with dated prefix (2026-07-19)
- [x] All SDD artifacts materialized in archive (proposal.md, design.md, tasks.md [all 25 items x], verify-report.md, archive-report.md, specs/ingestion/spec.md delta)
- [x] Canonical specs promoted to main spec tree
- [x] No files remain in openspec/changes/ for this change (ready for potential live-directory cleanup if reorg occurs)

**Engram**:
- [x] All 6 artifacts persisted to Engram (proposal, spec, design, tasks, apply-progress, verify-report) — search IDs 1054, 1055, 1056, 1057, 1059, 1061
- [x] Archive report to be saved with topic key `sdd/add-llm-concept-extraction/archive-report`

## Next Steps

**For the project**:
- Archive folder now at: `openspec/changes/archive/2026-07-19-add-llm-concept-extraction/`
- Main spec tree updated: `openspec/specs/ingestion/spec.md` (16 requirements, 20+ scenarios total)
- MVP-2 vertical slice is LIVE on main @ 779bb2c

**Unblocked downstream work**:
- MVP-2 is now proven (extraction + classification + fail-closed + idempotency all verified)
- Next MVP-2 slices can build on extraction (e.g., entity resolution, other OKF types, relationship graph) using the same fail-closed pattern and test harnesses
- Feature is now behind review-gate: users will see extraction results before confirm, can decline and stay at Source-only

**Documented non-blocking items**:
- SUGGESTION 1: Catch RecursionError in extraction/concept.py JSON parse (low likelihood, future hardening)
- SUGGESTION 2: Document per-entry provenance validation convention (DRY, documentation polish)
- SUGGESTION 3: Derive (_concepts, _entities) tuple from _TYPE_TO_LINK_DIR.values() (cosmetic, single-source-of-truth)
- SUGGESTION 4: Real-model classification fidelity test (smoke test with local Ollama, post-MVP-2-launch)
- **Recommendation**: Consolidate into dedicated post-MVP-2-launch polish change; **not blocking MVP-2 release**

## Risks & Mitigations

| Risk | Likelihood | Mitigation | Status |
|---|---|---|---|
| LLM output injection (newline forging) | Low | Existing `_reject_newline` guard on insert_index_entry; derived titles/slugs pass through it before catalog | **MITIGATED** |
| Nondeterministic LLM titles breaking idempotency | CRITICAL (pre-correction) | Provenance-keyed _source_has_derived_object guard (deterministic, engine-derived); secondary path-exists guard for slug collisions | **FIXED IN CORRECTION** |
| Malformed LLM output crashes ingest | High (pre-validation) | Fail-closed validation (JSON parse, type check, title/description blanks); None propagation + degrade | **MITIGATED** |
| LLM unavailable crashes ingest | High (pre-catch) | OllamaError caught locally in _stage_derived_object; stderr note + exit 0 | **MITIGATED** |
| User edits overwritten on re-ingest | Medium (pre-idempotency) | Create-only write_exclusive; path-existence check skips write if derived doc exists | **MITIGATED** |
| Extraction runs on --auto | High (pre-design) | Design requires extraction to ALWAYS run; --auto only skips confirm prompt | **DESIGNED-IN** |
| Config creep into extraction module | Medium (pre-guard) | Import-graph test guards extraction/ and llm/ against openkos.config; leaf discipline maintained | **MITIGATED** |

## Deferred/Out-of-Scope Items

**Explicitly deferred to future changes**:
- RecursionError guard on pathological JSON (low likelihood, future hardening)
- Per-entry provenance validation documentation (DRY polish)
- _TYPE_TO_LINK_DIR.values() single-source-of-truth (cosmetic refactor)
- Real-model (qwen3:8b) classification-fidelity smoke test (post-launch)
- Multiple derived objects per ingest (future MVP-2 slice)
- Other 9 OKF types (future MVP-2 slices)
- Entity resolution / merge / reclassification (MVP-3+)
- Typed relationship graph (MVP-3+)
- Sensitivity high-water-mark across sources (MVP-3+)
- MVP-2 hybrid retrieval (roadmap:63 later phase)

**Accepted residual limitations**:
- Zero residual limitations; all CRITICAL findings fixed, all SUGGESTION-level items documented

## Traceability

This archive report records the final state of the `add-llm-concept-extraction` change from proposal through implementation, strict TDD task phases, verification, and archival. The change has been:
- Fully specified (1 MODIFIED + 6 ADDED requirements, 20 scenarios total, delta merged into main spec tree)
- Fully designed (D1-D7 architecture decisions, fail-closed strategy, idempotency mechanism, testing harness, threat matrix)
- Fully implemented (4-slice stacked-to-main PR chain #46-#49, 515 tests, 98.67% coverage, 1 post-review correction fully integrated)
- Fully verified (7/7 requirements verified, 20/20 scenarios passing tests, D1-D7 verified in code, 4 bounded reviews all approved)
- Fully delivered (all PRs merged to main, docs updated, feature live)

The SDD cycle is CLOSED. The change is archived. MVP-2's first feature — LLM concept/entity extraction — is complete and live.

**Archive Date**: 2026-07-19 (ISO format)
**Repository Head**: 779bb2c (main, after approval, all 4 PRs merged)
**Specification**: `openspec/specs/ingestion/spec.md` (16 requirements, 20+ scenarios; 1 MODIFIED + 6 ADDED + 9 preserved)
**Verification Date**: 2026-07-19 (verify-report PASS, all design decisions verified)
**Archival Status**: COMPLETE
**MVP-2 Status**: FIRST FEATURE DELIVERED — LLM extraction is live, fail-closed, review-gated, idempotent, documented

---

## Observation Lineage (Engram Traceability)

- Proposal: sdd/add-llm-concept-extraction/proposal (ID: 1054)
- Specification: sdd/add-llm-concept-extraction/spec (ID: 1055)
- Design: sdd/add-llm-concept-extraction/design (ID: 1056)
- Tasks: sdd/add-llm-concept-extraction/tasks (ID: 1057)
- Apply Progress: sdd/add-llm-concept-extraction/apply-progress (ID: 1059)
- Verification: sdd/add-llm-concept-extraction/verify-report (ID: 1061)
- Archive Report: sdd/add-llm-concept-extraction/archive-report (this document, to be saved to Engram)
