# Proposal: Status-Aware Retrieval (Gap #8 · S1)

## Intent

The `status` frontmatter field is inert: written as `"active"` in `okf.py`
(`build_source_concept` L121, `build_concept` L191) but read nowhere in `src/`.
The `supersedes`/`reconciled_with` edges written by the S4 reconcile verb are
label-only — consumed solely for reconcile idempotency, read by no retrieval
path. A superseded or deprecated concept is therefore retrieved, ranked, and
answered from exactly like a live one. S1 makes lifecycle state finally govern
retrieval, and monetizes the reconcile arc's already-shipped supersedes edges.

## Scope

### In Scope (S1 only)
- Make `status: deprecated` concepts filtered or down-ranked in the
  query/retrieval path (query pool + adjudication/contradiction candidate loads).
- Give `supersedes` edges retrieval meaning: a superseded concept is treated as
  deprecated for retrieval purposes.
- Reuse the existing document-load seams (`okf._iter_docs`; query-time
  `retrieval/pool.py`, `fusion.py`, `graph_retrieve.py`, `answer.py`).

### Out of Scope (later slices in the Gap #8 arc — Non-Goals)
- **S2**: reference-aware `forget` + tombstones (scope/depth, dangling
  inbound-ref rewrite, tombstone marker).
- **S3**: sensitivity fail-closed pre-send filter across all **6** `llm.chat`
  call-sites (incl. `extract` and `query`, not just 4).
- **S4**: export surface with confidential exclusion.

## Capabilities

### New Capabilities
- `status-aware-retrieval`: how document `status` and `supersedes` edges gate
  visibility/ranking across the retrieval path.

### Modified Capabilities
- `reconcile-command`: conditional — only if follow-up #1619 (anchor-based
  conflict detection) folds into S1 (see decision point 2).

## Approach

Introduce one shared lifecycle predicate at the document-load seams (reusing the
`_iter_docs` choke-point pattern) that resolves each concept's effective status
from its `status` field AND inbound `supersedes` edges, then either drops or
demotes non-live concepts before fusion/answer. Query-time seams enforce the
same predicate so filtering is uniform across FTS, vector, and graph inputs.

## Open Decision Points (need sign-off before spec)
1. **Exclude vs. down-rank** deprecated/superseded concepts. Exclude = simplest,
   cleanest answers, but hides history and risks empty results when only a
   deprecated concept matches. Down-rank = keeps recall, needs a demotion weight
   and a tie/threshold rule. Recommendation: lean exclude for the default query
   path with a possible `--include-deprecated` escape, but this is yours to set.
2. **Fold #1619 or defer.** Anchor-based reconcile conflict detection is a
   write-verb concern, not retrieval; it expands S1 past its "status-aware
   retrieval" spine. Recommendation: defer to a reconcile follow-up unless you
   want it here.

## Arc / Dependency Note (context, not S1 work)
Gap #8 is a 4-slice arc: S1 status-aware retrieval (this) → S2 reference-aware
forget + tombstones → S3 sensitivity fail-closed filter → S4 export exclusion.
Slices are otherwise loosely coupled and independently shippable, with ONE hard
edge: **S3 MUST land before any cloud-backend or export slice** (incl. S4). No
cloud backend or export verb exists in code today, so lifecycle-first (S1) is
safe now — but record this dependency so it is not lost.

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Empty results when only deprecated concepts match | Med | Down-rank fallback or `--include-deprecated` escape (decision 1) |
| Inconsistent filtering across FTS/vector/graph inputs | Med | Single shared predicate applied at every load seam |
| `supersedes` cycles / self-supersede | Low | Treat cycles as live; guard self-reference |

## Rollback Plan

Revert is additive: the lifecycle predicate is a new read-side filter. Removing
it (or defaulting it to pass-through) restores today's status-blind retrieval
with no data migration — `status`/`supersedes` remain written as before.

## Dependencies

- Reconcile verb's `supersedes` edges (already shipped, S4 arc) — consumed, not
  modified.

## Success Criteria

- [ ] A `status: deprecated` concept is filtered or down-ranked in query results.
- [ ] A concept superseded via `supersedes` is treated as deprecated in retrieval.
- [ ] Adjudication/contradiction candidate loads honor the same lifecycle predicate.
- [ ] Live (`active`) retrieval behavior is unchanged.
