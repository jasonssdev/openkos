# Archive Report: Graph-Augmented Retrieval (MVP-2 Slice 4)

**Date**: 2026-07-21  
**Change**: graph-augmented-retrieval  
**Status**: ARCHIVED & CLOSED  

## Scope Shipped

- **Graph_rank PPR retriever** (`retrieval/graph_retrieve.py`): pure personalized PageRank over a GraphStore with undirected view, alpha=0.85, uniform personalization, seed-excluded, pool cap max(limit,10).
- **GraphHit dataclass** (added to `retrieval/fusion.py`): immutable (concept_id, score) for PPR ranking.
- **Optional 3rd graph list in fuse()** (`retrieval/fusion.py`): additive optional `graph_hits` param, no behavior change when None, byte-identical to two-list prior behavior.
- **Two-stage answer() retrieval** (`retrieval/answer.py`): initial fuse(fts,vec) → seeds=top min(limit,5) → build_graph+graph_rank → final fuse(fts,vec,graph)[:limit] → _assemble_context.
- **Additive AnswerResult fields**: graph_hit_count (int, default 0), graph_degraded (bool, default False).
- **Query CLI stderr extension** (`cli/main.py`): retrieval: line now reports graph_hit_count and includes degrade note when applicable.
- **Graph degrade contract**: broad except Exception → ([], graph_degraded=True) mirrors dense-degrade posture; FTS unaffected; no cold-start precondition.
- **Dependency added**: scipy>=1.14 (required by nx.pagerank's sole implementation path).
- **Three spec delta merges** (retrieval-fusion, query-answer, query-command): all additive, merged into main openspec/specs/ files.

## Merge & Verification

**Merged to main @ commit**: 17c04b3 (squash, PR #100)  
**Branch status**: clean, up to date on main  

**Verification gate** (independent, strict TDD — not trusting apply-progress):
- `uv run pytest -q` → 1161 passed, exit 0
- `uv run mypy .` (repo-wide incl. tests/) → Success: no issues found in 97 source files
- `uv run ruff check` → All checks passed!
- `uv run ruff format --check` → 97 files already formatted

**Task completion**: 13/13 checklist items marked [x] in tasks.md; 0 unchecked; all 5 phases complete.

## Review & Approval

**Bounded 4R review**: lineage `review-4c5ee2ec8dc87cb3`, tier HIGH, APPROVED, 0 blockers.  
**Verdict**: PASS  
**Key compliance checks**:
- AnswerResult: additive only (graph_hit_count, graph_degraded appended after dense_degraded; no existing fields removed/retyped).
- fuse(graph_hits=None): byte-identical to prior two-list behavior (test_graph_hits_none_is_byte_identical_to_two_list_fuse asserts equality).
- answer.py: config-free (no openkos.config import; AST guard unmodified and passing).
- retrieval/fusion.py: zero networkx imports (only docstring mentions).
- cli/main.py: no graph import (AST guard test test_cli_main_never_imports_graph_and_registers_no_graph_command still green).
- Spec-to-test compliance: 9 requirements, 18 scenarios, all 18 verified via passing non-vacuous tests.
- PPR determinism: pinned via test_determinism_across_repeated_calls in test_graph_retrieve.py (real fixture, real nx.pagerank invocation, exact GraphHit order equality).

## Deferred Non-Blocking Follow-ups

Identified during review; not blocking this archive. Recommended for a future slice:

1. **Per-query in-process graph rebuild cost**: build_graph + PPR run on every seeded query with no cache/persist/size-cap; same pattern as FTS build_index. A future perf slice could cache/persist the graph or cap size.

2. **test_empty_question_never_calls_build_graph_or_graph_rank is WEAK**: does not spy on build_graph/graph_rank directly (behavior IS covered by properly-spied no-seeds sibling test). Strengthen or merge into one test.

3. **DRY: pool cap max(limit,10) computed in 3 places**: answer.py x2 + graph_retrieve.py. Hoist to one constant in a future refactor.

4. **Tie-break determinism test fragility**: ties in float PPR scores pinned to exact order from scipy/networkx versions. May become brittle across dependency updates; consider tie-break strategy agnostic of exact score magnitude in future.

## Still-Open Older Follow-ups

From prior slices (carry forward, unrelated to this slice's scope):

- Reindex prune-skip observability (deferred from Slice 3 or earlier).
- Reindex WRITE-path sqlite WAL/busy_timeout + embed batch chunking (deferred from dense-retrieval slice).
- Embedding-model-tag provenance in vector_meta (deferred from vector-store slice).
- Typed/weighted-edge PPR (edges are unweighted today; future enhancement).

## Artifact Observation IDs (Engram)

For traceability, the following artifact observations are bound to this archive:

- `sdd/graph-augmented-retrieval/proposal` (ID: 1433)
- `sdd/graph-augmented-retrieval/spec` (ID: 1435)
- `sdd/graph-augmented-retrieval/design` (ID: 1436)
- `sdd/graph-augmented-retrieval/tasks` (ID: 1437)
- `sdd/graph-augmented-retrieval/verify-report` (ID: 1439)

## Archive Structure

```
openspec/changes/archive/2026-07-21-graph-augmented-retrieval/
  ├── archive-report.md (this file)
  ├── proposal.md
  ├── design.md
  ├── tasks.md
  └── specs/
      ├── retrieval-fusion/spec.md (delta)
      ├── query-answer/spec.md (delta)
      └── query-command/spec.md (delta)
```

## Specs Merged

All three delta specs have been merged into the main openspec/specs/ directory:

1. **openspec/specs/retrieval-fusion/spec.md**: Added 3 requirements (Optional Third Graph List, Omitted Graph List Byte-Identical, Three-List Deterministic) with 6 scenarios.

2. **openspec/specs/query-answer/spec.md**: Added 3 requirements (Graph Retrieval Runs As Second-Stage, Graph Retrieval Degrades, Personalized PageRank Deterministic) and updated 2 existing requirements (AnswerResult Carries Retrieval Metadata, Empty Query Sets Distinct No-Match Cause) with additive graph fields and guard coverage.

3. **openspec/specs/query-command/spec.md**: Updated 1 requirement (Stderr Retrieval Summary) to include graph_hit_count and degrade note.

## Source of Truth Updated

The following main specs now reflect the new graph-augmented retrieval behavior:

- `openspec/specs/retrieval-fusion/spec.md` — 3-list RRF fusion
- `openspec/specs/query-answer/spec.md` — two-stage retrieval, graph seeding, degrade contract
- `openspec/specs/query-command/spec.md` — extended stderr reporting

Existing callers of `fuse(fts, vec)` and `answer()` without graph parameters remain byte-identical to Slice 3 behavior.

## SDD Cycle Complete

The change has been fully planned (proposal), specified (delta specs), designed (architecture decisions), implemented (5 sequential phases, 13/13 tasks), verified (1161 tests, 0 blockers), and now archived. The change is ready for production use and requires no follow-up actions in this slice.

**Ready for the next change.**
