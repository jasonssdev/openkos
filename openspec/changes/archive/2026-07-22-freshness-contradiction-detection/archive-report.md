# Archive Report: Contradiction Detection (S3 of freshness-lint-v1)

**Change**: freshness-contradiction-detection
**Archive Date**: 2026-07-22
**PR**: #111 (commit c856b85, squash merge)
**Status**: ARCHIVED — SDD cycle complete

## Change Summary

Contradiction Detection (S3 of freshness-lint-v1) adds a read-only, advisory `contradictions` CLI verb that judges already-related concept pairs in a bundle's graph for factual conflict. The engine is a config-free leaf (`src/openkos/resolution/contradiction.py`) that derives deduped typed-edge candidate pairs, judges each via LLM, and returns verdicts with confidence and cited claims. Precision is first-class: (1) candidates restricted to typed edges (few, high-signal pairs), (2) conflicting claims citation required or verdict degrades to UNCERTAIN, (3) high-confidence CONTRADICTS only shown by default. Zero writes, fully reversible, no schema/persistence changes.

## Delivery

| Item | Details |
|------|---------|
| **PR Number** | #111 |
| **Commit** | c856b85 (squash merge to main) |
| **Branch** | feature/freshness-lint-v1/s3-contradiction-detection |
| **Merge Strategy** | Squash to main |
| **Commit Count** | 2 commits (feat + boundary tests) |
| **Lines Changed** | 2,151 total (365 engine + 127 CLI verb + 645 leaf tests + 615 CLI tests) |
| **Review Budget** | size:exception (1150–1300 lines, High risk, mirrored S2b pattern) |
| **Date Merged** | 2026-07-22 |

## Verification

| Aspect | Status | Evidence |
|--------|--------|----------|
| **All Tasks** | PASS | 24/24 checked ([x]) in tasks.md |
| **Full Test Suite** | PASS | 1458 tests passed in 3.40s |
| **New Functionality Tests** | PASS | 71 new tests (52 engine leaf + 19 CLI) |
| **Spec Requirements** | PASS | 10/10 requirements, 10/10 scenarios covered |
| **Static Analysis** | PASS | ruff check, ruff format --check, mypy all clean |
| **Determinism** | PASS | No network I/O; pair ordering proven deterministic |
| **No Regression** | PASS | No prior-slice files touched (edge_typing.py, volatility_typing.py, adjudication.py, sqlite_graph.py untouched) |
| **Verdict** | **PASS** | Ready for archive (post-apply bounded review completed) |

## Spec Compliance

All 10 specification requirements met with passing test coverage:

1. **Candidate Generation From Typed Graph Edges, Deduped** — Symmetric/multi-edge pairs collapse to one via frozenset dedup.
2. **Per-Pair Verdict Shape With Cited Claims** — Verdict, confidence, rationale, and cited conflicting_claims present.
3. **Citation-Gated Precision** — CONTRADICTS without claims degrades to UNCERTAIN.
4. **Fail-Closed Reply Parsing And Confidence Coercion** — Malformed replies degrade one pair only; confidence clamped [0,1].
5. **Pair Cap With Explicit Truncation Notice** — Candidates capped at 200; cap reached reported explicitly.
6. **Read-Only CLI Verb, High-Confidence Default** — Zero writes; default shows only CONTRADICTS with confidence >= 0.7.
7. **`--all` Reveals Every Verdict** — Flag shows all verdicts without affecting find_contradictions logic.
8. **Degrade-On-No-Model 3-Tier Catch** — OllamaUnavailable → OllamaModelNotFound → generic OllamaError, each with message + zero writes.
9. **Empty Graph Yields Clear Message** — No typed edges → "No candidate pairs found." exit 0, no llm call.
10. **Deterministic Candidate Pair Ordering** — Pairs sorted deterministically by key across runs.

## Code Artifacts

### New Files
- `src/openkos/resolution/contradiction.py` (365 lines) — Engine leaf: graph read, dedup/cap, per-pair judge, fail-closed parse.
- `tests/unit/resolution/test_contradiction.py` (645 lines) — Comprehensive unit tests for engine leaf.
- `tests/unit/cli/test_contradictions.py` (615 lines) — CLI verb tests + 3-tier error handler validation.

### Modified Files
- `src/openkos/cli/main.py` (+127 lines) — New `contradictions` verb cloning `adjudicate` wiring; `--all` display flag; CLI imports only `contradiction`, never `openkos.graph`.

### No Changes To
- ADR directory — **ADR-0007 (Volatility Taxonomy) NOT modified**. No new ADR created (proposal gate: read-only, zero persistence).
- Prior-slice files (S1/S2): edge_typing.py, volatility_typing.py, adjudication.py, sqlite_graph.py all untouched.

## SDD Artifacts

All change artifacts archived in this folder:
- `proposal.md` — Intent, scope, approach, risks, ADR evaluation.
- `design.md` — Technical approach, architecture decisions, data flow, testing strategy.
- `tasks.md` — 24 implementation tasks (6 phases) — all checked complete.
- `verify-report.md` — Full verification with spec matrix, scope confirmation, deviation analysis.
- `specs/contradiction-detection/spec.md` — Specification copied from change folder to archive.

## New Canonical Capability

**`contradiction-detection`** — A read-only, config-free precision layer over graph-typed edges that judges each already-related concept pair for factual conflict via LLM, advisory-only.

**Canonical Spec Location**: `/Users/jasonssdev/Dev/Projects/openkos/openspec/specs/contradiction-detection/spec.md`

**Spec Status**: Created by sdd-archive phase (delta spec from change folder copied to canonical location).

## Architecture & Design Decisions

- **Candidate Signal**: Typed-edge pairs only (relation_type is not None) → few, high-signal, not O(n·k).
- **Dedup Strategy**: `frozenset({source_id, target_id})` → A→B and B→A judged once.
- **Pair Ordering**: Sorted by `tuple(sorted(pair))` → deterministic stable prefix under cap.
- **Threshold/Cap**: Module constants (CONFIDENCE_DISPLAY_THRESHOLD = 0.7, MAX_PAIRS = 200), not config knobs (mirrors S2 pattern).
- **Parse Machinery**: Module-local clone of adjudication fail-closed helpers (D4 — no cross-import of `_`-prefixed symbols).
- **CLI Graph Access**: Verb imports only `contradiction` leaf, never `openkos.graph` (D2/D6 boundary).

## Follow-Up Work

The following items were identified during development but deferred:

1. **#1592 — read_config Hardening** — Enhanced error messages and recovery for missing/invalid openkos.yaml. Related to freshness-lint-v1 broader initialization safety.
2. **#1606 — Parse Helper Dedup Refactor** — Extract shared JSON/verdict/confidence parsing helpers (currently replicated in contradiction.py + adjudication.py + edge_typing.py) into a shared module. Logged as deferred dedup optimization, not a correctness issue.

**S4 (Guided Reconcile)** — Next slice in freshness-lint-v1 arc (not yet planned; awaits S3 closure).

## Review & Issues

- **CRITICAL Issues**: None.
- **WARNING Issues**: None.
- **SUGGESTION Issues**: None.
- **Deviations**: 4 implementation-level refinements (all spec-compliant, documented in verify-report.md):
  1. find_contradictions returns tuple[list, int] (cap signal required by tasks.md 4.4).
  2. Public is_high_confidence_contradiction() helper (consistency with D4 convention).
  3. _pair_relation_types() helper (enriches LLM prompt per design contract).
  4. Dangling-edge test replaced by _load_doc unit tests (vacuous case vs. reachable guarantee).

## Engram Artifact Traceability

All SDD artifacts stored in persistent memory (Engram) for full traceability:

- **#1599** — sdd/freshness-contradiction-detection/proposal
- **#1600** — sdd/freshness-contradiction-detection/spec
- **#1601** — sdd/freshness-contradiction-detection/design
- **#1602** — sdd/freshness-contradiction-detection/tasks
- **#1604** — sdd/freshness-contradiction-detection/verify-report
- **[NEW]** — sdd/freshness-contradiction-detection/archive-report (this document)

## Archive Structure

This archive folder contains:
```
openspec/changes/archive/2026-07-22-freshness-contradiction-detection/
├── proposal.md                           (change intent, scope, approach)
├── design.md                             (technical decisions, data flow)
├── tasks.md                              (24 implementation tasks, all checked)
├── verify-report.md                      (full verification, spec compliance matrix)
├── specs/
│   └── contradiction-detection/
│       └── spec.md                       (canonical spec — copied verbatim from change folder)
└── archive-report.md                     (this file)
```

## Final Notes

- **Original Change Folder**: `openspec/changes/freshness-contradiction-detection/` — NOT removed (requires orchestrator action; executor cannot remove folders).
- **Canonical Spec**: NOW exists at `openspec/specs/contradiction-detection/spec.md` (created by archive phase).
- **ADR Status**: No ADR changes required or made (read-only advisory verb, zero persistence).
- **SDD Cycle**: Complete. Change fully planned, implemented, verified, and archived.

---

**Archive Date**: 2026-07-22
**Archived By**: sdd-archive executor
**Archive Mode**: hybrid (artifacts persisted to both Engram + filesystem)
