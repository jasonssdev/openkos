# Archive Report: graph-projection-reuse

**Date Archived**: 2026-07-27
**Status**: ARCHIVED
**GitHub Issues**: #197, #196, #195 (All Closed)

| Slice | PR | Merged as |
|---|---|---|
| PR1 — Shared vectors.db fixture | [#201](https://github.com/jasonssdev/openkos/pull/201) | `701af58` |
| PR2 — Store reuse, CLI restructuring, docstrings | [#202](https://github.com/jasonssdev/openkos/pull/202) | `c3dc332` |

## Summary

`build_graph` rebuilds an in-memory SQLite projection over every concept doc on each call. Three CLI commands paid that walk more than once per invocation or claimed they did:

- `suggest-relations` / `contradictions`: on the zero-result path they built twice — once in `candidate_edges`/`find_contradictions`, then again via `_zero_edge_state_message` → `graph_edge_summary` (#196).
- `status`: rebuilt the whole projection every run to print one edge-state line (#195).
- Tests: three divergent copies of the same `vectors.db`-seeding helper (#197).

The fix is an additive optional `store: GraphStore | None = None` keyword on the three readers. When omitted (every existing call site), behavior is byte-identical to today. When supplied, the reader reuses the caller's already-open store and skips its own `build_graph` call. CLI commands open `build_graph` once per invocation and thread it through.

An additional correctness fix: `suggest-relations`' zero-result counts are now computed over the same candidates-seeded projection the filtering ran over, rather than a separate candidates-free build. This is an intended, accepted behavior change documented by a new test and by a comment on #196.

## Verification

Two separate review lineages, both approved:

| Slice | Receipt | Outcome | Test Coverage |
|---|---|---|---|
| PR1 #201 | `review-4b297527c58cff6e` | Approved | 2242 passed, 97.57% |
| PR2 #202 | `review-7e3f91a6029ebff3` | Approved | 2254 passed, 97.58% |

Both received high-risk (4R lens) review. PR1 had one readability warning (an artifact count discrepancy in documentation, since corrected). PR2 had one reliability suggestion (an imprecise docstring mechanism, since fixed). Both went through zero correction rounds. Final gates: 2254 tests passing, branch coverage 97.58% against a 90% floor, mypy and ruff clean.

## Specs merged

The delta spec added two requirements to `graph-projection`:

| Requirement | Change | Scenarios |
|---|---|---|
| Caller-Supplied Store Reuse Within One Invocation | ADDED | 4 scenarios: use supplied store, preserve default behavior, reader never closes, zero-result path shares one build |
| Summary Over A Caller-Supplied Store Reflects That Store's Projection | ADDED | 2 scenarios: `suggest-relations` zero-result reflects candidates-seeded counts, `contradictions` typed-count unaffected |

All 6 scenarios verified by real, non-vacuous test evidence. The two new requirements were merged into `openspec/specs/graph-projection/spec.md` per the established house style.

## Key design decisions (Architecture)

1. **Keyword name `store`**: matches every existing local variable name; type annotation already carries "graph".
2. **Reuse mechanism: two-branch early return**, not `nullcontext` or a shared helper — structurally guarantees a reader never closes a caller-supplied store (grep shows zero `store.close()` in any reader).
3. **`_zero_edge_state_message.store` is required, not optional** — a forgotten keyword becomes a `TypeError` at test collection, not a silent rebuild.
4. **Store lifetime designed out, not guarded**: every reader call sits lexically inside a single `with build_graph(...) as store:` block per invocation.
5. **Accepted behavior delta**: `suggest-relations`' zero-result counts now reflect the candidates-seeded store (may be higher than a candidates-free build). Intended, correct, documented by a new test and a GitHub comment on #196.
6. **#197 as a factory fixture** in `conftest.py`, not an autouse fixture — preserves the opt-in-per-test seeding and keeps the existing `seed_vectors_db(tmp_path)` call shape.
7. **One-build assertion via real pass-through wrapper**, not a `MagicMock` — invocation-wide count + real output verification, robust against unrelated refactors.
8. **No ADR**: additive, default-preserving, no external consumers, trivially revertible.

## Corrections to prior work

Two corrections made during implementation:

1. **Four stale layering docstrings corrected**: `resolution/edge_typing.py:25-28`, `resolution/contradiction.py:25-28`, `cli/main.py:4912-4915`, `cli/main.py:5217-5219`. All claimed "`cli/main.py` MUST NOT import `openkos.graph` directly" or "never imports `openkos.graph` directly," which was false as of `cli/main.py:33-35` (already importing `openkos.graph.summary`). The actual, tested constraint is narrower: canonical layer (`model`/`bundle`/`state`) MUST NOT import `openkos.graph`; no `graph` CLI verb can be registered. `cli/main.py` importing `openkos.graph` is established practice (already done by `query` and `reindex`).

2. **Issue #195 premise corrected**: the issue claimed `status` builds the projection twice. It does not. `status` performs THREE independent bundle walks (`survey_bundle`, `lint_check.collect_docs`, `build_graph`) and exactly ONE `build_graph` call. The issue body was wrong; the title was right. #195 delivers parameter plumbing (status owns the store lifetime) and a docstring correction, not a speedup. Consolidating the three walks is a bigger refactor, deferred.

## Known limitations

Recorded rather than fixed:

- The proximity similarity floor (0.70) was calibrated on 7 documents / 21 pairs. It shows a real separation exists; it does not justify more precision than that. Recalibrate against a real fixture bundle before treating the constant as settled.
- `status` now calls `build_graph` once per invocation (via the shared-store parameter), but still performs its THREE independent bundle walks (survey, lint, graph). Consolidating them is out of scope.
- `suggest-relations`' zero-result counts are now higher when proximity rows exist, reflecting the candidates-seeded store. This is an accepted, intended delta, not a regression.

## Deliverables

All artifacts archived to `/Users/jasonssdev/Dev/Projects/openkos/openspec/changes/archive/2026-07-27-graph-projection-reuse/`:

- `proposal.md` — Intent, scope, capabilities, affected areas
- `explore.md` — Current state, approaches, sequencing, risks
- `design.md` — Verified locations, keyword contracts, CLI reshaping, test strategy, architectural decisions
- `tasks.md` — Phase-by-phase work units, all 31 code/test/verification tasks complete (1 documentation task deferred: GitHub comment on #196, posted when PR2 merged)
- `verify-report.md` — PR1 verification (2242 tests, 97.57% coverage, PASS)
- `verify-report-pr2.md` — PR2 verification (2254 tests, 97.58% coverage, 6/6 spec scenarios met, PASS)
- `specs/graph-projection/spec.md` — Main spec with 2 ADDED requirements + 6 scenarios merged in

## Engram observation IDs (for traceability)

- Proposal: #2012
- Delta Spec: #2013
- Design: #2014
- Tasks: #2016
- Verify Report (PR1): (embedded in final verification run)
- Verify Report (PR2): #2018

## SDD Cycle Complete

The change has been fully planned (proposal), explored, designed, implemented in two chained PRs, verified (both PRs passed independent review and test gates), and archived. All spec requirements met by real test evidence. All implementation tasks complete. Ready for the next change.
