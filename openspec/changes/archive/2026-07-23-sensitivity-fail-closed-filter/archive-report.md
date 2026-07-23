# Archive Report: Sensitivity Fail-Closed Filter (Gap #8 · S3)

**Change**: sensitivity-fail-closed-filter  
**Status**: COMPLETE — archived 2026-07-23  
**Deliverables**: Feature PR #116 (S3a+S3b spine + correction batch) + Hygiene PR #117 (S3c) both merged to main

## Executive Summary

Sensitivity-fail-closed-filter implements a cross-cutting fail-closed predicate governing which concepts reach `llm.chat` across all six LLM-calling sites. The change is delivered via two merged PRs (#116 S3a/S3b + correction batch; #117 S3c hygiene), with all tasks marked complete, all 1672 tests passing, and four critical 4R review findings addressed in a bounded correction batch.

## Change Overview

### Scope: S3a Spine + S3b Seams + S3c Hygiene

**S3a (Predicate Spine, PR #116)**  
- New `src/openkos/sensitivity.py` leaf: shared fail-closed predicate `sensitive_concept_ids(bundle_dir, *, threshold="confidential")` computing concept ids at/above sensitivity threshold in one walk  
- Wiring at four S1-pattern seams: `query` (retrieval/answer.py hit seam), `contradictions` (resolution/contradiction.py), `adjudicate` (resolution/adjudication.py::run), `suggest-relations` (resolution/edge_typing.py)  
- `--include-confidential` escape flag added to query, contradictions, adjudicate, suggest-relations CLI commands

**S3b (Divergent Seams, PR #116 — same PR)**  
- LintDoc.sensitivity field removed (dead code; suggest-volatility uses shared predicate, not per-doc field)  
- suggest-volatility seam wiring with `--include-confidential` flag  
- Extract floor gate: `cfg.default_sensitivity: confidential` skips llm.chat entirely  
- `_assemble_context` defense-in-depth post-filter for per-doc re-check (walk-independent)  
- `--include-confidential` escape flag added to suggest-volatility and ingest CLI commands

**S3c (Hygiene, PR #117)**  
- New `src/openkos/llm/parsing.py` exposing public `extract_json_object`/`extract_json_items` (5 clones consolidated into shared module)  
- Migration of 5 call sites (adjudication, edge_typing, volatility_typing, contradiction, extraction/concept) from private clones to shared imports  
- `config.py` defensive fix: except clause now catches (yaml.YAMLError, TypeError) for robustness against unhashable complex YAML keys

### Correction Batch (Post-4R Review, Part of PR #116)

Four CONFIRMED adversarial review findings addressed with bounded edits:

1. **R1 Risk (Fail-Open)**  
   - Failure: bare `okf._rank("")` resolves to `"private"`, bypassing confidential floor gate  
   - Fix: new shared `sensitivity.blocks_llm_send(value, *, threshold)` authority treating absent/blank as blocked BEFORE delegating to `_rank`  
   - Impact: every fail-closed gate now uses the same authority (predicate walk + extract floor + _assemble_context re-check)

2. **R2 Readability (Dead Field)**  
   - Failure: LintDoc.sensitivity field populated but never read (suggest-volatility filters via shared predicate)  
   - Fix: removed field, its population, two isolated tests, unused factory parameter  
   - Verification: grep confirmed zero readers outside tests

3. **R4 Resilience (Walk-Bypass Leak)**  
   - Failure: okf._iter_docs walk can silently miss a subtree (unlistable directory), leaving a doc invisible to the predicate yet still directly readable by known path  
   - Fix: _assemble_context gained independent per-doc re-check via blocks_llm_send (walk-independent defense-in-depth at the actual send point)  
   - Scope: query/answer only (other seams' candidates never bypass the walk, recorded as follow-up)

4. **R3 Reliability (Docs Gap)**  
   - Failure: contradictions, adjudicate, suggest-relations, suggest-volatility had zero CLI documentation  
   - Fix: added four new sections to docs/cli.md with flag tables, matching existing style  
   - Also: documented --include-confidential in query and ingest sections alongside --include-deprecated

## Locked Decisions

1. **Threshold = confidential-only**: Block only sensitivity=confidential; private+public sent unchanged. Missing/malformed/unreadable/unknown-type all fall back to confidential and block. Rationale: default floor is private, so any threshold at or below private would block essentially every doc.

2. **Extract seam = pre-bundle floor gate**: extract runs on raw source prior to concept-bundling and has no per-doc sensitivity. Instead gates on cfg.default_sensitivity: if confidential, skip llm.chat; if private/public, proceed unchanged.

3. **Fail-closed semantics**: Unlike lifecycle.py (fail-safe, skip on doubt), sensitivity gates fail-closed (block on doubt). Opposite directions separated into distinct modules (lifecycle.py vs sensitivity.py) to prevent invariant erosion.

## Shipped Implementation vs. Spec

The shipped behavior precisely matches the specification:

- **Fail-Closed Sensitivity Resolution**: `blocks_llm_send` function (shared authority) treats absent/blank as blocked before delegating to okf._rank; all read errors/parse errors blocked; unknown values ranked as confidential then blocked  
- **Private and Public Pass Through**: concepts with sensitivity: private/public sent unchanged to all six call sites  
- **Uniform Enforcement**: all six seams (query, contradictions, adjudicate, suggest-relations, suggest-volatility, extract) filter before llm.chat — four via lifecycle.filter_hits reuse (S1-pattern), two via direct plumbing (LintDoc field removed per correction batch; extract floor gate)  
- **Exclusion, Not Redaction**: _assemble_context post-filter + blocks_llm_send ensure no partial content of blocked concepts ever reaches llm.chat  
- **--include-confidential Escape**: every verb carrying --include-deprecated now carries sibling flag, restoring sensitivity-blind behavior when present

## Verification Results

**Test Coverage**: 1672 tests passed (baseline 1657 + 15 net new)  
- +10 tests from correction batch (C.1 blocks_llm_send matrix, C.4 walk-bypass defense-in-depth)  
- -2 tests removed (C.7 dead LintDoc.sensitivity field)  
- +14 tests from S3c parsing.py (behavior-first, only public API tested)  
- +1 test from S3c config.py TypeError hardening

**Type & Style Checks**:  
- `uv run mypy .`: Success (119 source files, zero issues)  
- `uv run ruff check .`: All checks passed  
- `uv run ruff format --check .`: 119 files already formatted

**Phase Verification**:
- Phase 1+2 (S3a/S3b): PASS (1649 tests baseline, +8 correction batch tests, 1657 final)  
- Phase 3 (S3c): PASS (306 focused tests + 1672 whole-tree; stale docstring at resolution/adjudication.py:162 is a SUGGESTION, not blocker)  
- Combined verdict: PASS — spec requirements matched, no regressions, correction batch findings addressed

## Artifact Engram Traceability

| Artifact | Observation ID | Type | Created |
|----------|-----------------|------|---------|
| Proposal | #1687 | architecture | 2026-07-23 06:51:41 |
| Specification | #1688 | architecture | 2026-07-23 06:54:01 |
| Design | #1689 | architecture | 2026-07-23 06:56:33 |
| Tasks | #1690 | architecture | 2026-07-23 06:59:16 |
| Apply Progress | #1691 | architecture | 2026-07-23 07:17:52 |
| Verify Report | #1692 | architecture | 2026-07-23 07:32:08 |
| Archive Report | (this observation) | architecture | 2026-07-23 |

## Canonical Spec Location

**Created**: `openspec/specs/sensitivity-aware-llm/spec.md`  
- Reflects shipped behavior: blocks_llm_send shared fail-closed authority, six-site uniform enforcement, per-doc re-check defense-in-depth, --include-confidential escape flag  
- No delta markers retained; canonical form omits proposal/design working notes

## Known Follow-ups (Harden Before Cloud/Export Slice)

1. **Performance**: repeated okf._iter_docs walks in lifecycle.py, sensitivity.py, and lint.collect_docs — perf double/triple walk penalty. Future work: share one canonical walk pass across all three axes.

2. **Directory-Walk Observability**: okf._iter_docs can silently drop unlistable subtrees. Currently mitigated for query/answer via _assemble_context re-check (C.4/C.5), but before cloud/export slice a bundle-wide observability signal is needed. Follow-up: surface okf._walk_errors at the workspace level.

3. **Cosmetic Docstring Drift** (SUGGESTION, not blocker): `resolution/adjudication.py:162` references deleted private clone name `concept._extract_json_items` instead of `openkos.llm.parsing.extract_json_items`. Zero functional impact; safe to fix opportunistically in a follow-up.

4. **S4 Export and Right-to-Be-Forgotten**: exclusion of confidential concepts from export slice and deferred purge of sensitive data remain future work (gap #8 S4).

## Delivery Summary

- **Feature PRs**: #116 (S3a + S3b + correction batch), #117 (S3c hygiene) — both merged to main  
- **Review Workload**: ~880 authored lines across three slices; Medium risk (per-slice <400; co-merge <800)  
- **Test Evidence**: 1672 passed (CI-equivalent), mypy + ruff checks clean  
- **Rollback Boundary**: each PR reverts independently; sensitivity frontmatter writing untouched by all changes  
- **Readiness**: ready for the next gap (#8 S4 export slice) once follow-ups are hardened

## Signed Off

Archive phase executor: Claude (SDD archive skill)  
Date: 2026-07-23  
Scope: complete change cycle from proposal through merged PRs to archive  
Next: gap #8 S4 export confidential-exclusion slice (future work)
