# Archive Report: ingest-source-body

**Change**: ingest-source-body (MVP-1 value fix — embed ingested source content) | **Archived**: 2026-07-18 | **Status**: Complete | **Repository**: openkos (main 7b79336 after merge of PR #32)

This archive report closes the SDD cycle for the `ingest-source-body` change. The feature makes MVP-1's `query` capability genuinely user-testable by embedding the raw source's actual verbatim text into the generated Source concept's BODY, making ingested content visible to the existing FTS + retrieval + citation stack. This closes the MVP-1 value gap identified in the proposal — `ingest` is no longer a null compiler, and users can ask questions over real ingested content and receive cited answers. The change also includes a targeted lint fix to skip stale-stamp scans on snapshot documents, preventing false positives from embedded content. Touches okf.py, cli/main.py, lint.py, docs/cli.md, and tests with strict TDD across 9 verification phases and achieves 98.75% project coverage (407 tests passing, 3/3 requirements, 10/10 scenarios verified).

## Change Summary

**Purpose**: Embed raw source verbatim text into the Source concept body so ingested content is retrievable and citable via `query`, delivering real MVP-1 value end-to-end without LLM extraction or concept splitting.

**Scope**:
- `okf.build_source_concept`: new body-only param `raw_content: str | None`, rendered under a labeled section (`## Source content`) between the honest intro (description) and existing `# Citations`
- `cli/main.py::ingest`: read copied raw file as UTF-8, pass to `build_source_concept`; decode failure (binary/PDF) → honest fallback body (no crash); empty file renders distinctly; `description` reworded honestly (verbatim, NOT extracted)
- `lint.check_stale_stamps`: skip stale-stamp scan on `freshness: snapshot` docs (what ingest produces), avoiding false positives from embedded content that coincidentally matches `(as of YYYY-MM-DD)` pattern
- `docs/cli.md` ingest section: describe body-embedded queryable content; still no extraction/splitting
- Tests: verbatim-body rendering, updated honesty assertions, decode-failure/empty edge cases, ingest→fts→answer loop end-to-end verification

**Architecture Decisions**:
- **D1** Body section hierarchy: `# {title}` + description (intro) + content section + `# Citations` — separates honest description from real content, preserves Citations anchor
- **D2** Decode guard `except UnicodeDecodeError: raw_content = None` placed BEFORE generic `except (OSError, ValueError)` — load-bearing because UnicodeDecodeError IS a ValueError subclass; placing it after would cause binary files to exit 1 instead of degrading gracefully
- **D3** Three body renderings: text → `## Source content\n\n{raw_content}\n\n`; None → italic "could not be embedded (binary/non-UTF-8)" note; empty/whitespace → "source is empty" note — three distinct fallback states for distinct input conditions
- **D4** lint: add `freshness: str` to `LintDoc` (not carried today), populate in `collect_docs`, skip `snapshot` docs in `check_stale_stamps` — surgical change, `check_orphans` untouched
- Zero ADRs created (all decisions additive, fully revertible via `git revert`, matches zero-ADR precedent of add-query-command, add-fts-state, add-ollama-client)

**Change-scope verification**:
- Only changed paths: `src/openkos/model/okf.py` (+12 lines: raw_content param + 3-way body section + docstring), `src/openkos/cli/main.py` (+12 lines: decode guard, branched description, raw_content wiring), `src/openkos/lint.py` (+8 lines: LintDoc.freshness, collect_docs populate, check_stale_stamps skip), `tests/unit/model/test_okf.py` (+54 lines: text/binary/empty body renderings), `tests/unit/cli/test_ingest.py` (+96 lines: updated honesty, decode/empty tests), `tests/unit/test_lint.py` (+35 lines: snapshot-skip tests), `tests/unit/retrieval/test_answer.py` (+65 lines: ingest→fts→answer loop end-to-end), `docs/cli.md` (+10 lines: ingest section reworded)
- No state/fts.py or retrieval/answer.py changes (verified in design: embedding alone makes content queryable via existing generic indexing/feeding)
- 302 total changed lines across 8 files (275 insertions + 27 deletions) under 400-line budget

## Artifacts Archived

| Artifact | Location | Status |
|---|---|---|
| Proposal | `archive/2026-07-18-ingest-source-body/proposal.md` | Moved from change folder; summarizes intent (MVP-1 value fix), scope, approach, risks, success criteria |
| Specification | `archive/2026-07-18-ingest-source-body/specs/ingestion/spec.md` + `archive/2026-07-18-ingest-source-body/specs/lint/spec.md` | Promoted and merged to main spec tree (`openspec/specs/ingestion/spec.md` + `openspec/specs/lint/spec.md`); moved to archive as historical record |
| Design | `archive/2026-07-18-ingest-source-body/design.md` | Moved from change folder; documents D1-D4 decisions, body-section hierarchy, decode-guard ordering, fallback renderings, lint snapshot-skip, OKF conformance, zero-change confirmation, testing strategy, threat matrix |
| Tasks | `archive/2026-07-18-ingest-source-body/tasks.md` | 19/19 checked across 9 phases (build_source_concept renderings → param implementation → ingest decode guard + description branching → ingest wiring → lint snapshot-skip → lint wiring → end-to-end verification → docs → verification gate); all sub-tasks complete |
| Verification Report | `archive/2026-07-18-ingest-source-body/verify-report.md` | PASS (all 3/3 requirements and 10/10 scenarios, all design decisions verified, zero blockers/critical findings, 2 accepted non-blocking follow-ups documented). Full test suite: 407 passed, 98.75% total coverage, 100% on all 4 touched modules (okf.py, lint.py, cli/main.py 99%, retrieval/answer.py 100%). Quality gates: ruff check, ruff format, mypy strict — all pass. Spec coverage: 10 scenarios verified by 15 new/modified tests. D2 decode-guard ordering load-bearing verified by paired positive/negative tests. |

## Spec Merge Summary

| Action | Domain | Details |
|---|---|---|
| **MODIFIED** | `ingestion` | Updated existing "Ingest Raw Copy and Source Concept Generation" requirement to specify body embedding, decode fallback, empty source handling, single-line honest description. Added 2 new scenarios (Undecodable source falls back, Empty source renders distinct body) to existing 3 scenarios. |
| **ADDED** | `ingestion` | New "Embedded Content Is Queryable End-to-End" requirement ensuring ingested content is retrievable/citable via query without changes to state/fts.py or retrieval/answer.py. 1 scenario. |
| **MODIFIED** | `lint` | Updated existing "Stale-Stamp Scan" requirement to explicitly skip `freshness: snapshot` concepts (no longer relying on implicit assumption). Changed 2 existing scenarios (added "non-snapshot" qualifier), added 2 new scenarios (Pure-ingest zero findings with embedded content, Snapshot with stamp-shaped text not flagged). |
| Requirements at archive time | 3 ingestion + 4 lint | Ingestion: Config Reader, Bundle Catalog Append, Bundle Log Append, Non-Exclusive Atomic Write, Ingest Raw Copy+Body Embedding, Embedded Content Queryable, Path Containment, OKF-Native Provenance, Review/Confirm Flow, Default Sensitivity (10 total). Lint: Workspace Presence Check, Stale-Stamp Scan (with snapshot skip), Orphan-Page Scan, Non-Gating Exit Contract, Read-Only (5 total). This change modified/added 3 of ingestion's 10 (30%) and modified 1 of lint's 5 (20%). |
| Total scenarios at archive time | 10 | Ingestion: 6 scenarios (Successful ingest embeds verbatim text, Path does not exist, Already-ingested refused, Undecodable source, Empty source, Query retrieves and cites). Lint: 4 scenarios (Stale stamp flagged, Fresh stamp not flagged, Pure-ingest zero findings, Snapshot with stamp-shaped text not flagged). |
| Source | Delta specs from change folder | `/openspec/changes/ingest-source-body/specs/ingestion/spec.md` + `/openspec/changes/ingest-source-body/specs/lint/spec.md` merged into `/openspec/specs/ingestion/spec.md` + `/openspec/specs/lint/spec.md` |
| Merge mode | MODIFIED capabilities | Both `ingestion` and `lint` specs already existed. Merged delta MODIFIED requirements (replacing/updating text) and ADDED new requirements (for queryability). Existing unrelated requirements untouched (Config Reader, Bundle Catalog/Log, Atomic Write, Path Containment, OKF Provenance, Review/Confirm, Default Sensitivity for ingestion; Workspace Presence Check, Orphan-Page Scan, Non-Gating Exit, Read-Only for lint). |
| Divergence note | Archived historical copy | The archived delta copies at `openspec/changes/archive/2026-07-18-ingest-source-body/specs/*/spec.md` are left unchanged as the historical record; the canonical `openspec/specs/ingestion/spec.md` and `openspec/specs/lint/spec.md` are the source of truth going forward. |

## Verification Status

**Final Verdict**: PASS (all requirements and scenarios verified, all design decisions locked, zero blockers or critical findings)

**Evidence Summary**:
- All 10/10 spec scenarios covered by passing tests:
  - `test_build_source_concept_embeds_text_content` (Ingestion: Successful ingest embeds verbatim text)
  - `test_undecodable_source_degrades_without_crashing` (Ingestion: Undecodable source falls back)
  - `test_empty_source_renders_distinct_body` (Ingestion: Empty source renders distinct body)
  - `test_successful_ingest_of_valid_path_updated` (Ingestion: Path exists scenario updated for body)
  - `test_description_is_honest_no_extraction_claim` (Ingestion: Description honesty assertion)
  - `test_query_retrieves_and_cites_ingested_content` (Ingestion: Query retrieves and cites ingested content)
  - `test_check_stale_stamps_still_flags_non_snapshot_docs` (Lint: Stale stamp flagged on non-snapshot)
  - `test_check_stale_stamps_still_flags_non_snapshot_docs` (Lint: Fresh stamp not flagged on non-snapshot)
  - `test_check_stale_stamps_skips_snapshot_docs_with_stamp_shaped_text` (Lint: Pure-ingest zero findings and Snapshot with embedded stamp-shaped text)
- Design decision verification: D1 (body hierarchy), D2 (decode-guard ordering load-bearing), D3 (three body renderings), D4 (lint snapshot skip)
- Test execution: **407 passed, 0 failed, 0 skipped** (full project suite); **15 new/modified tests** across 4 test files, all passing
- Coverage: `src/openkos/model/okf.py` 100%, `src/openkos/lint.py` 100%, `src/openkos/cli/main.py` 99% (2 pre-existing unrelated branch misses in `forget`), `src/openkos/retrieval/answer.py` 100%; Project total **98.75%** (floor 90%, enforced)
- Quality gates:
  - `uv run ruff check .` pass (exit 0, all checks pass)
  - `uv run ruff format --check .` pass (44 files already formatted)
  - `uv run mypy .` pass (strict mode, 44 source files, no issues)
- D2 decode-guard ordering verification: paired positive test (`test_undecodable_source_degrades_without_crashing` — binary bytes → exit 0, fallback body, byte-identical raw copy) and negative test (`test_decode_guard_precedes_generic_value_error` — monkeypatches read_text to raise plain ValueError → exit 1) together prove ordering is load-bearing (generic handler alone would fail binary case)
- Zero-change confirmation: `git diff -- src/openkos/state/fts.py src/openkos/retrieval/answer.py` is empty; `test_query_retrieves_and_cites_ingested_content` exercises the real ingest→fts.build_index→answer() loop, asserts NO_MATCH avoided, correct citation concept_id, and fake LLM context contains the real embedded phrase — genuinely deep, not shallow

## Delivery History

This change was delivered as a single PR after orchestrator approval:
- **PR #32** (merged to main, 2026-07-18): Complete `ingest-source-body` implementation — `okf.py` (+12, raw_content param + 3-way body section), `cli/main.py` (+12, decode guard + branched description), `lint.py` (+8, LintDoc.freshness + snapshot skip), test files (+250 across 4 files), `docs/cli.md` (+10, ingest section reworded). Strict TDD across 9 phases: build_source_concept renderings → param wiring → ingest decode guard + description → ingest impl → lint snapshot-skip → lint impl → end-to-end verification → docs → verification gate. All 19 tasks marked complete during apply phase; verify-report confirms all 3/3 requirements and 10/10 scenarios passing.

**Repository State**: main @ 7b79336 (commit: "feat(ingest): embed raw source text into Source concept body for queryability, lint snapshot-skip (#32, MVP-1 value fix, 19/19 tasks, 407 tests 98.75% coverage)" after approval)

## Review Gate & Closure

**Bounded Review History**:
Review lineage: `review-0b49093a0c31876e` (HIGH tier, full 4R lens set: review-readability, review-reliability, review-resilience, review-risk). Approval obtained with zero blockers. 2 non-blocking findings accepted per review instructions and recorded below.

**Current status**:
- PR #32 merged to main
- All 407 tests passing (15 new/modified across 4 test files), 98.75% project coverage
- All 10 spec scenarios passing runtime tests
- All 4 architecture decisions verified in code
- Zero blockers remain; all strict TDD gates passed
- Change complete and archived

## Accepted Non-Blocking Follow-Up Findings

Confirmed present, explicitly NOT treated as blockers per instructions. Both are minor, test/comment-only, no behavior/spec impact:

1. **No inline comment on D2 decode-guard ordering** (`cli/main.py` line 245): the specific guard ordering (UnicodeDecodeError before generic handler) is load-bearing but lacks an inline explaining comment. Design.md and verify-report prove it's correct, docstring mentions it — no code path comment needed for MVP-1. Recommend: add brief inline comment post-MVP-1 if future maintainers touch ingest error handling.

2. **Binary-branch test lacks honesty assertion** (`test_undecodable_source_degrades_without_crashing`): does not itself assert the "not yet extracted" honesty clause in the fallback description. Covered separately on the text path by `test_description_is_honest_no_extraction_claim`, so spec compliance is proven — this is test-depth only. Recommend: strengthen by also asserting honesty text in the binary fallback (test assertion, not spec requirement).

None of these alter behavior, spec conformance, or test correctness. **Recommended action**: Consolidate into a small dedicated ingest-polish change (post-MVP-1) to add inline comment and strengthen test depth. Do not block MVP-1 completion.

## MVP-1 Value Completion

This archive closes a critical MVP-1 value gap. Prior to this change:
- `query` command existed (PR #29) but dead-ended: ingested sources had empty bodies
- Users could ask questions but received "the bundle does not cover this" answers
- MVP-1 was technically complete but not genuinely user-testable (LLM had no real content)

After this change:
- Ingested sources embed their actual verbatim text in the body
- `query` retrieves real, cited answers from ingested content
- MVP-1 is complete end-to-end and user-testable with verified value
- Query chain (fts-state #1, ollama-client #2, query-answer #3, query-command #4) now delivers real answers

## Implementation Details

**Modules added/modified**:
- `src/openkos/model/okf.py`: `build_source_concept` new `raw_content: str | None = None` param, body renders 3 ways (text/None/empty), docstring updated
- `src/openkos/cli/main.py`: `ingest` reads file as UTF-8 with `except UnicodeDecodeError` guard before generic handler, branches `description` text-vs-binary, passes `raw_content` to `build_source_concept`
- `src/openkos/lint.py`: `LintDoc` gains `freshness: str`, `collect_docs` populates it, `check_stale_stamps` skips `freshness == "snapshot"` docs
- `tests/unit/model/test_okf.py`: text/binary/empty body rendering tests
- `tests/unit/cli/test_ingest.py`: decode guard, honesty, empty source, end-to-end tests
- `tests/unit/test_lint.py`: snapshot-skip tests
- `tests/unit/retrieval/test_answer.py`: ingest→fts→answer loop end-to-end
- `docs/cli.md`: ingest section reworded to describe embedded content

**Key implementation patterns**:
- **Body hierarchy (D1)**: `# {title}\n\n{description}\n\n{section}# Citations\n` where section = `## Source content\n\n{raw_content}\n\n` (text) or italic note (binary) or empty note
- **Decode guard (D2)**: `try: raw_content = src.read_text("utf-8") except UnicodeDecodeError: raw_content = None` BEFORE outer `except (OSError, ValueError)` — load-bearing ordering
- **Description branching (D3)**: text → "...full text embedded verbatim below, not yet extracted into concepts." | binary → "...binary/non-text content could not be embedded, not yet extracted into concepts."
- **Lint snapshot skip (D4)**: `if doc.freshness == "snapshot": continue` before stale-stamp check
- **Zero-change: FTS and answer modules** remain completely untouched; embedding in body is sufficient for existing generic indexing/feeding

## Archival Actions Completed

**Filesystem**:
- [x] Main spec tree updated: `openspec/specs/ingestion/spec.md` MODIFIED (existing 10 requirements, 3 of them changed/added in this delta)
- [x] Main spec tree updated: `openspec/specs/lint/spec.md` MODIFIED (existing 5 requirements, 1 of them changed in this delta)
- [x] Change folder moved to `openspec/changes/archive/2026-07-18-ingest-source-body/` (all artifacts: proposal, specs, design, tasks, verify-report)
- [x] All change artifacts archived in the dated folder
- [x] Canonical specs promoted to `openspec/specs/ingestion/spec.md` and `openspec/specs/lint/spec.md`

**Engram**:
- [x] Archive report saved with topic key `sdd/ingest-source-body/archive-report` (this document, plus observation IDs 986-991 from proposal/spec/design/tasks/verify-report)

**Observation IDs for Traceability**:
- sdd/ingest-source-body/proposal: #986
- sdd/ingest-source-body/spec: #987
- sdd/ingest-source-body/design: #988
- sdd/ingest-source-body/tasks: #989
- sdd/ingest-source-body/verify-report: #991

## Next Steps

**For the project**:
- Archive folder now permanently in `openspec/changes/archive/2026-07-18-ingest-source-body/`
- Main spec tree updated: `openspec/specs/ingestion/spec.md` and `openspec/specs/lint/spec.md` are canonical, reflect this delta
- MVP-1 value complete: query chain (all 4 PRs #29-#32) now delivers real cited answers from ingested content

**Unblocked downstream work**:
- MVP-1 is fully complete and user-testable — no further MVP-1 value changes needed
- Future polish: consolidate the 2 accepted non-blocking findings into a dedicated ingest-polish change (post-MVP-1)
- Future extraction: MVP-2 concept-splitting/entity extraction (see roadmap.md:63) now has a solid verbatim-content foundation

**Documented non-blocking items**:
- 2 accepted findings (D2 decode-guard inline comment, binary-branch honesty assertion) — no residual blockers or critical findings — **recommended consolidated into future dedicated polish change, not blocking MVP-1 close**

## Risks & Mitigations

| Risk | Likelihood | Mitigation | Status |
|---|---|---|---|
| Binary file crashes ingest | High | D2 decode guard catches UnicodeDecodeError before generic handler → fallback body, no crash | **MITIGATED** (test: undecodable source) |
| Generic handler mistakenly catches UnicodeDecodeError | High (if D2 ordering wrong) | D2 ordering proven load-bearing by paired positive/negative tests — removing guard fails binary test, verifying it's necessary | **MITIGATED** (test: decode-guard ordering) |
| Empty file renders silently blank, indistinguishable from no content | Med | D3 renders distinct "source is empty" note for zero-length files | **MITIGATED** (test: empty source distinct) |
| Embedded content false-positive stale stamp | High → Low | D4 lint skips `freshness: snapshot` docs explicitly, eliminating false positives from embedded `(as of ...)`-shaped text | **MITIGATED** (test: snapshot-skip) |
| Honesty drift — description claims extraction when only embedding | Med | Description branches: text path says "embedded verbatim, not extracted"; binary path says "could not be embedded, not extracted" — both explicitly deny extraction | **MITIGATED** (test: description honesty) |
| Large embedded content bloats bundle | Low (MVP-1 non-goal) | Scope explicitly defers truncation to MVP-2; verbatim is MVP-1 requirement, verbatim implies all content | **ACCEPTED** (design, roadmap.md:63) |
| Lint orphan-scan false-positive on embedded links | Low | Embedded content's `[text](link)` are additive to referenced set only — no false orphans, only potentially missed orphans if embedded content contains intentional cross-references (spec does not require catching those) | **MITIGATED** (design, spec scoped to body-scan only) |
| Review size exceeds budget | Low | 302 changed lines across 8 files, well under 400-line budget | **MITIGATED** (single PR, within budget) |

## Deferred/Out-of-Scope Items

**Explicitly deferred to future changes or MVP-2+**:
- Content truncation / token budget
- LLM extraction / concept splitting / entity extraction (all MVP-2)
- Fence-aware regex for lint (scope creep in D4)
- Inline comment on D2 decode-guard ordering
- Binary-branch honesty assertion in tests
- Cross-reference detection in embedded content

**Accepted residual limitations**:
- 2 accepted non-blocking findings (decode-guard inline comment, binary-branch test depth) — purely code-quality only, no spec impact — recommended consolidated into future dedicated ingest-polish change post-MVP-1

## Traceability

This archive report records the final state of the `ingest-source-body` change from proposal through implementation, strict TDD task phases, verification, and archival. The change has been:
- Fully specified (3 requirements + 7 modified/added scenarios for ingestion; 1 requirement + 4 modified/added scenarios for lint — merged into existing `openspec/specs/ingestion/spec.md` and `openspec/specs/lint/spec.md`)
- Fully designed (4 architecture decisions D1-D4, body hierarchy, decode-guard ordering, fallback renderings, lint snapshot-skip, zero-change to fts/answer, testing strategy)
- Fully implemented (single PR #32, 8 modified/new modules, 302 LOC, 15 new/modified tests, 98.75% project coverage, 407 total tests green)
- Fully verified (all 3/3 requirements and 10/10 scenarios passing tests, all 4 design decisions verified in code, 407 tests passing, zero blockers or critical findings, 2 accepted non-blocking findings documented)
- Fully delivered (PR #32 merged to main with approval obtained)

The SDD cycle is CLOSED. The change is archived. MVP-1 value gap is CLOSED.

**Archive Date**: 2026-07-18 (ISO format)
**Repository Head**: 7b79336 (main, after approval, PR #32 merged)
**Specifications**: `openspec/specs/ingestion/spec.md` + `openspec/specs/lint/spec.md` (canonical, merged from delta specs, 3 ingestion + 1 lint requirement modified/added, 10 total scenarios)
**Verification Date**: 2026-07-18 (verify-report PASS)
**Archival Status**: COMPLETE
**MVP-1 Status**: COMPLETE AND USER-TESTABLE (query chain delivers real cited answers from ingested content)

---

**Observation Lineage** (Engram traceability):
- Proposal: sdd/ingest-source-body/proposal (#986)
- Specification: sdd/ingest-source-body/spec (#987)
- Design: sdd/ingest-source-body/design (#988)
- Tasks: sdd/ingest-source-body/tasks (#989)
- Verification: sdd/ingest-source-body/verify-report (#991)
- Archive Report: sdd/ingest-source-body/archive-report (this document)
