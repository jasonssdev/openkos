# Archive Report: graph-projection

**Change**: graph-projection (MVP-2 slice 1) | **Archived**: 2026-07-19 | **Status**: Complete | **Repository**: openkos (main a655f62) | **Mode**: hybrid

This archive report closes the SDD cycle for the `graph-projection` change, the first derived-layer slice of MVP-2: a read-only, in-memory SQLite node-edge projection over the bundle's existing untyped markdown links, exposed through a `GraphStore` Protocol and convertible to an `nx.DiGraph`.

## Change Summary

**Purpose**: Lay the substrate for later MVP-2 slices (hybrid retrieval, graph-based lint, relation typing) without committing to a relation vocabulary yet. `graph/` is the first package in a new derived layer, sitting alongside (never inside) the canonical `model`/`bundle`/`state` layers.

**Scope shipped**:
- New `src/openkos/graph/` package: `__init__.py`, `base.py`, `sqlite_graph.py`, `analysis.py`.
- In-memory SQLite (`:memory:`) node-edge projection, rebuild-per-run, context-managed lifecycle mirroring `state/fts.py`.
- Node identity = OKF concept id (bundle-relative path minus `.md`).
- Edges extracted via a scoped regex over bundle-relative `[text](/path.md)` links; `relation_type` column present but always `NULL` this slice.
- `GraphStore` Protocol (`nodes()`/`edges()`/`neighbors()`, no path-finding method) mirroring `llm/base.py::LLMBackend`'s structural-typing style.
- `analysis.to_digraph(store) -> nx.DiGraph` conversion using NetworkX.
- `networkx>=3.4` added to runtime deps, `types-networkx` to dev deps.
- AST-based layering guard tests: `model`/`bundle`/`state` never import `openkos.graph`; no `graph` CLI command exists.

**Key reconciled decisions** (captured during spec/design revision, confirmed in source at verify time):
- **Dangling/non-bundle-relative links produce no edge, not an error.** Edge extraction filters link targets to only those resolving to a known node id in the same projection *before* insert. External URLs, links without a leading `/`, non-`.md` targets, and links to concept ids absent from the projection are all silently dropped — building never raises. Surfacing dangling links as a lint concern is explicitly deferred to a later slice.
- **`GraphStore` Protocol excludes a path-finding method.** `base.py` exposes only `nodes()`/`edges()`/`neighbors()` (adjacency), keeping it a stdlib-only leaf with no NetworkX import. Path finding is obtained exclusively via `analysis.py`'s `to_digraph()` conversion and NetworkX's own graph algorithms — not via the Protocol surface.
- **`relation_type` stays a nullable, unpopulated column this slice.** The schema reserves the attach point for future typed-edge work without requiring a later migration; no vocabulary or extraction path is committed now.

## Artifacts Archived

| Artifact | Location | Status |
|---|---|---|
| Proposal | `archive/2026-07-19-graph-projection/proposal.md` | Moved from change folder; Engram `sdd/graph-projection/proposal` (id 1148) |
| Specification (delta) | `archive/2026-07-19-graph-projection/specs/graph-projection/spec.md` | Merged to main spec tree at `openspec/specs/graph-projection/spec.md`; Engram `sdd/graph-projection/spec` (id 1149) |
| Design | `archive/2026-07-19-graph-projection/design.md` | Moved from change folder; Engram `sdd/graph-projection/design` (id 1150) |
| Tasks | `archive/2026-07-19-graph-projection/tasks.md` | 18/18 checked, all 4 phases complete; Engram `sdd/graph-projection/tasks` (id 1151) |
| Delivery decision | (recorded below) | Engram manual observation (id 1152) — feature-branch-chain, 4 units |
| Apply progress (post-verify follow-up) | (recorded below) | Engram `sdd/graph-projection/apply-progress` (id 1153) |
| Verification Report | (recorded below) | Engram `sdd/graph-projection/verify-report` (id 1154) |

## Spec Merge Summary

| Action | Domain | Details |
|---|---|---|
| **CREATED** | `graph-projection` | New spec domain created at `openspec/specs/graph-projection/spec.md` (no prior main spec existed for this capability) |
| Requirement count | 8 requirements + 13 scenarios | Full specification of the projection's contract: in-memory SQLite build, node identity, edge extraction (incl. dangling-link handling), nullable `relation_type`, `GraphStore` Protocol surface, NetworkX conversion, read-only guarantee, layering boundary |
| Source | Delta spec from change folder | `openspec/changes/graph-projection/specs/graph-projection/spec.md` → `openspec/specs/graph-projection/spec.md` |
| Merge mode | Direct copy (no pre-existing spec, delta already authored in living-spec shape — no ADDED/MODIFIED delta headers to convert) | First spec for this domain; delta becomes canonical main spec verbatim |

No requirements or scenarios were invented, dropped, or altered during merge — the delta spec (Engram id 1149, reconciled revision) was already structured as a standalone capability spec and is preserved faithfully in `openspec/specs/graph-projection/spec.md`.

## Verification Status

**Final Verdict** (Engram `sdd/graph-projection/verify-report`, id 1154): VERIFIED WITH NOTES, later closed — see apply-progress (id 1153) for the follow-up that resolved both CRITICAL gaps before this archive.

**Evidence Summary** (reproduced independently at verify time, then re-reproduced after the follow-up commit):
- `uv run pytest` → 648 passed (646 baseline + 2 follow-up tests), exit 0
- `uv run ruff check .` → "All checks passed!", exit 0
- `uv run ruff format --check .` → "60 files already formatted", exit 0
- `uv run mypy .` → "Success: no issues found in 60 source files" (mypy strict), exit 0
- `uv run pytest --cov=openkos.graph --cov-branch --cov-report=term-missing tests/unit/graph/` → 43 passed; **100.00% branch coverage** on `openkos.graph` (124 stmts / 24 branches, 0 missed; gate ≥ 90%). Per-file: `__init__.py` 100%, `analysis.py` 100% (8 stmts/2 branches), `base.py` 100% (15 stmts/0 branches), `sqlite_graph.py` 100% (101 stmts/22 branches)
- 18/18 tasks checked across 4 TDD phases (Phase1 3/3, Phase2 8/8, Phase3 2/2, Phase4 5/5)
- 11/13 spec scenarios PASS with a covering runtime test at initial verify; the 2 remaining CRITICAL-flagged scenarios ("projection never touches disk" and "building writes nothing to the bundle bytes/mtime") were satisfied by code inspection but lacked a runtime test — both closed by 2 added tests (`test_build_graph_never_touches_disk`, `test_build_graph_writes_nothing_to_the_bundle_bytes_and_mtime_unchanged`) in the post-verify follow-up (apply-progress id 1153), with zero production code changes (no defect existed; these were legitimate triangulation tests over already-correct behavior)
- All 8 requirements / 13 scenarios in `openspec/specs/graph-projection/spec.md` are covered by a passing runtime test after the follow-up

**Issues at final close**:
- 0 CRITICAL (both closed by the post-verify follow-up)
- 2 WARNING (non-blocking): TDD Cycle Evidence table in apply-progress fully detailed only for Unit 4 (Units 1-3 detail lost to topic_key upsert overwrite — audit-trail gap, not a code defect; git history shows 4 clean separate `feat(graph):` commits confirming unit boundaries); 3 unrelated uncommitted doc modifications were present on the verify-time branch (out of scope for this change, tracked separately under docs PR #71)
- 0 SUGGESTION outstanding (the 1 suggestion from verify — add the 2 missing tests — was implemented)

## Delivery History

Delivered as a **4-unit Feature Branch Chain** (delivery decision: Engram manual observation id 1152), chosen because the tasks forecast (id 1151) estimated ~600-700 changed lines against the 400-line review budget (`ask-on-risk` fired, high risk). PR1 targeted the tracker branch `feat/graph-projection`; each child PR targeted the immediate previous PR branch; only the tracker merged to `main`.

1. **PR #67** — `GraphStore` Protocol + `Edge` frozen dataclass + `networkx`/`types-networkx` dependency additions (base=tracker)
2. **PR #68** — `build_graph`/`SqliteGraphStore` build lifecycle + edge extraction + TOCTOU/no-disk guards (base=PR #67; largest unit)
3. **PR #69** — `nodes()`/`edges()`/`neighbors()` query surface ordering (base=PR #68)
4. **PR #70** — `analysis.to_digraph` NetworkX conversion + AST-based layering-boundary guard tests, plus the 2 post-verify follow-up tests closing the CRITICAL gaps (base=PR #69)

All 4 units landed to `main` via **PR #72** (the tracker/integration PR), merged as commit **a655f62**.

**Repository State**: `main` @ `a655f62`.

## Deferred / Non-Goals (explicit, unchanged from proposal through archive)

The following remain explicitly out of scope for `graph-projection` and are deferred to later MVP-2 slices or beyond, per the proposal's Out of Scope section and the spec's Non-Goals:

- Cross-source entity resolution or reversible merge of derived objects.
- Hybrid vector retrieval (sqlite-vec / Sentence Transformers).
- Relation-type extraction/NLP and the frontmatter-vs-prose typing vocabulary decision — `relation_type` remains a nullable, unpopulated reserved column.
- A CLI `graph` verb.
- Persistence of the projection to `.openkos/openkos.db` (or any `state/db.py`-style shared connection) — the projection stays `:memory:` and rebuild-per-run.
- import-linter / CI-enforced layering — the canonical/derived boundary remains a followed convention, verified by AST-based tests and manual review, not a CI guard.
- Dangling/orphan-link detection as a first-class lint feature — this slice only ensures dangling links are silently ignored during edge extraction (no edge, no raise); surfacing them to the user is reserved for a later slice.

## Risks & Limitations Recorded

| Risk | Likelihood | Status |
|---|---|---|
| Relation-typing ambiguity has no populate path yet | Med | Accepted — nullable column reserves the attach point; vocabulary decision explicitly deferred |
| Layering not CI-enforced | Med | Accepted — docstring boundary notes + AST-based tests + reviewer verification stand in until import-linter is wired |
| Rebuild-per-run performance at scale | Low | Accepted pre-alpha; matches `state/fts.py` precedent; a persisted path is schema-compatible (zero migration) if needed later |
| Regex misses/over-matches unusual link forms | Low | Mitigated — scoped to bundle-relative `/….md` links only; unit fixtures cover edge forms (external URL, no leading `/`, non-`.md`, dangling target) |
| Audit-trail gap: Units 1-3 detailed TDD evidence lost to topic_key upsert | Low | Recorded as WARNING in verify-report; git history (4 separate `feat(graph):` commits) independently confirms unit boundaries were respected |
| 3 unrelated uncommitted doc edits present on the verify-time branch | Low | Out of scope for this change; tracked separately under docs PR #71 (not touched by this archive) |

## Archival Actions Completed

**Filesystem**:
- [x] Living spec created at `openspec/specs/graph-projection/spec.md` (new capability domain; direct copy from the reconciled delta, format matched against `openspec/specs/fts-state/spec.md` and `openspec/specs/ingestion/spec.md`)
- [x] Change artifacts (proposal, design, tasks, specs) written to `openspec/changes/archive/2026-07-19-graph-projection/`
- [x] This archive report written to `openspec/changes/archive/2026-07-19-graph-projection/archive-report.md`

**Engram**:
- [x] Archive report saved with topic key `sdd/graph-projection/archive-report`
- [x] All artifact observation IDs recorded above for traceability (proposal 1148, spec 1149, design 1150, tasks 1151, delivery-decision 1152, apply-progress 1153, verify-report 1154)

## Known Limitation of This Archival Pass

The executor performing this archive had access only to `Read`/`Write`/`Edit`/`Glob` and Engram tools — no shell/`git` tool was available in this session. Every artifact was therefore **written as a copy** to the archive and living-spec locations rather than moved with `git mv`, and the original `openspec/changes/graph-projection/` directory could **not** be deleted by this executor. A follow-up step with shell/`git` access is required to run `git mv`-equivalent cleanup (or delete the original `openspec/changes/graph-projection/` directory) so the active changes directory no longer lists this change, and to stage/commit the rename so history is preserved as a move rather than an add+orphaned-original. Content-wise, the archive is complete and byte-faithful to the source change folder.

## Next Steps

**For the project**:
- `graph-projection` unblocks the next MVP-2 slice(s): hybrid retrieval, graph-based lint, and eventual relation-type extraction, all of which can now build on the `GraphStore` Protocol and `analysis.to_digraph` conversion.
- The deferred non-goals above (entity resolution/merge, hybrid vector retrieval, relation-type extraction, CLI `graph` verb, persistence) remain open follow-up candidates for future MVP-2 change proposals.
- Docs PR #71 (embeddings/Ollama reconciliation content in `docs/architecture.md`, `docs/roadmap.md`, `docs/tech_stack.md`) is a separate, still-under-review change and was intentionally not touched by this archive.

**For archive verification**:
- No CRITICAL issues remain; both flagged at initial verify were closed by the post-verify follow-up (id 1153) before this archive.
- The only outstanding action is the mechanical `git mv`/deletion of the original change folder noted above.

## Traceability

This archive report records the final state of the `graph-projection` change from proposal through verification and archival. The change has been:
- Fully specified (8 requirements, 13 scenarios)
- Fully designed (5 architecture decisions, ADR gate correctly did not fire — all decisions cheaply reversible)
- Fully implemented (4-unit feature-branch-chain, PRs #67-#70, landed via #72)
- Fully verified (100% branch coverage on `openkos.graph`, mypy strict clean, 648/648 tests passing, 0 CRITICAL issues remaining)
- Fully delivered (main @ a655f62)

The SDD cycle is CLOSED. The change is archived and ready for the next change, pending the mechanical folder-removal follow-up noted above.

**Archive Date**: 2026-07-19 (ISO format)
**Repository Head**: a655f62 (main)
**Archival Status**: COMPLETE (content), PENDING (original-folder removal — requires shell/git access not available to this executor)
