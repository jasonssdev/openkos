# Design: Status-Aware Retrieval (Gap #8 · S1)

## Technical Approach

Introduce ONE shared, canonical-layer lifecycle predicate,
`src/openkos/lifecycle.py::deprecated_concept_ids(bundle_dir) -> frozenset[str]`,
computed from a single live-bundle `okf._iter_docs` walk. Every retrieval
input and candidate-load surface filters its OWN concept-id list against that
one set BEFORE fusion / candidate emission. The default excludes deprecated
and superseded concepts; a threaded `include_deprecated` flag skips the
predicate entirely (no walk, no filter), restoring today's status-blind
behavior byte-for-byte.

**Verdict on the central decision: query-time filtering, not index-time
exclusion.** Deprecated concepts stay fully present in `fts.db`, `vectors.db`,
and `graph.db`; enforcement is a query-time set-difference at the
candidate-pool level. Justification is decisive on three independent grounds
(see ADR-1 below): (1) the LOCKED `--include-deprecated` flag must work
WITHOUT a reindex — index-time exclusion would make the flag require a full
rebuild; (2) two of the five surfaces (contradiction, adjudication) already
read the LIVE bundle fresh, never the persisted indexes, so an index-time
filter could not cover them and would force a second, forked predicate —
violating "single shared predicate"; (3) reading live frontmatter `status`
makes the filter track the current bundle, not the last reindex snapshot,
which is strictly more correct.

This is retrieval-side only. No change to how `status`/`supersedes` are
written (proposal Non-Goals). No new persisted schema. Fully reversible.

## Architecture Decisions

| Decision | Choice | Rejected alternative | Rationale |
|----------|--------|----------------------|-----------|
| Enforcement time | Query-time candidate-pool filter | Index-time exclusion in `reindex` | `--include-deprecated` must toggle WITHOUT reindex; contradiction/adjudication never read persisted indexes; live status beats snapshot status (ADR-1) |
| Predicate shape | One `frozenset[str]` of deprecated ids, computed once per command | Per-candidate graph lookup | O(N) single walk + set ops; avoids N queries per candidate (spec: efficient effective-status) |
| Predicate home | New leaf `src/openkos/lifecycle.py` (imports only `openkos.model.okf`) | Put in `retrieval/` or `resolution/` | Both `retrieval` AND `resolution` consume it; a package-root leaf over `okf` (like `lint.py`/`config.py`) avoids retrieval↔resolution coupling and any cycle |
| Supersedes source | `okf.decode_relations`, `type == "supersedes"` from frontmatter | Read `graph.db` typed edges | Same source the graph's typed-edge pass uses (`_populate_graph_tables`), so predicate ⇔ graph agree by construction; and no dependency on `graph.db` presence |
| Self-reference guard | Drop edges where `source == target` before set-building | Post-hoc removal | Spec: a self-`supersedes` never marks a concept deprecated |
| Mutual-cycle guard | Reciprocal cancellation: `(s,t)` supersedes `t` only if `(t,s)` is NOT also present | Mark every supersedes target | Spec: two concepts that mutually supersede are BOTH live |
| Filter helper | One generic `filter_hits(hits, deprecated)` over `.concept_id` | Per-input bespoke filters | `FtsHit`/`VecHit`/`GraphHit` all expose `.concept_id`; one helper, applied at every seam (spec: no single input leaks) |
| Flag threading | `include_deprecated: bool = False` on `answer`, `find_contradictions`, `find_candidates`; `--include-deprecated` on `query`, `contradictions`, `adjudicate` | Flag on `query` only | MUST on retrieval-facing `query`; consistent opt-in across all three read verbs at trivial cost |

## The Shared Predicate (single seam)

`src/openkos/lifecycle.py` — canonical leaf, `okf` + stdlib only:

```python
def deprecated_concept_ids(bundle_dir: Path) -> frozenset[str]:
    status_by_id: dict[str, str] = {}
    supersedes: set[tuple[str, str]] = set()   # (source, target), source != target
    for scan in okf._iter_docs(bundle_dir):
        if scan.read_error is not None or scan.parse_error is not None:
            continue
        cid = scan.path.relative_to(bundle_dir).with_suffix("").as_posix()
        meta = scan.metadata or {}
        status_by_id[cid] = str(meta.get("status") or "")
        try:
            relations = okf.decode_relations(meta)
        except ValueError:            # malformed relations: contribute no edges
            relations = []
        for r in relations:
            if r.type == "supersedes" and r.target != cid:   # self-ref guard
                supersedes.add((cid, r.target))
    superseded = {t for (s, t) in supersedes if (t, s) not in supersedes}  # mutual-cycle guard
    own_deprecated = {cid for cid, st in status_by_id.items() if st == "deprecated"}
    return frozenset(own_deprecated | superseded)


def filter_hits[H](hits: list[H], deprecated: frozenset[str]) -> list[H]:
    return [h for h in hits if h.concept_id not in deprecated]  # H has .concept_id
```

**Effective-status resolution** (spec Requirement 1): a concept is deprecated
iff its own `status == "deprecated"` OR it is a superseded target. One walk
builds the status map and the supersedes edge set; the deprecated set is two
set operations. No per-candidate lookup — meets "avoid N queries per
candidate."

**Cycle/self-reference handling** (spec Requirement 1 scenarios): `source ==
target` edges are dropped at collection; a 2-cycle `A↔B` cancels reciprocally
(neither is a superseded target). See Risk R2 for cycles of length ≥ 3, which
the spec does not enumerate.

## Data Flow

### `answer()` (query path) — filter early, once

    _fts_search ─→ hits           _dense_search ─→ vec_hits
        │                              │
        └──────────────┬───────────────┘
                       │  if not include_deprecated:
                       │     deprecated = lifecycle.deprecated_concept_ids(bundle_dir)
                       │     hits     = lifecycle.filter_hits(hits, deprecated)
                       │     vec_hits = lifecycle.filter_hits(vec_hits, deprecated)
                       ▼
              fuse(hits, vec_hits) ─→ seeds = top min(limit,5)   # seeds already live-only
                       │
                       ▼
              _graph_search(seeds) ─→ graph_hits
                       │  if not include_deprecated:
                       │     graph_hits = lifecycle.filter_hits(graph_hits, deprecated)
                       ▼
              fuse(hits, vec_hits, graph_hits)[:limit] ─→ assemble ─→ llm.chat

Filtering hits/vec_hits BEFORE the initial fuse means graph SEEDS are already
live-only; filtering `graph_hits` after PPR drops any deprecated node PPR
surfaced. Both fuses and all downstream counts therefore see only live ids —
no leak via any single input (spec Requirement 4).

**Live-concept-via-deprecated-neighbor** (spec Req 4 scenario, `D → C`): the
edge stays in the graph structure (no rebuild), so `C` still propagates PPR
mass and may surface on its own merits; `D` is removed from `graph_hits`
output and never appears as a hit. Confirmed by design.

**Only-match-is-deprecated** (spec Req 2 scenario): filtered `hits` and
`vec_hits` are both empty → `_classify_no_match` returns `"zero_hits"` →
standard `NO_MATCH`, exit 0. This is the documented, expected outcome, not an
error.

### Candidate-load surfaces — filter at emission

- **Contradiction** (`find_contradictions`): after `_candidate_pairs(store)`,
  drop any pair whose either id is in `deprecated_concept_ids(bundle_dir)`
  (unless `include_deprecated`). The superseded concept never appears in a
  candidate pair (spec Req 2 scenario). `bundle_dir` is already in scope.
- **Adjudication** (`find_candidates`): remove deprecated ids from
  `_iter_eligible`'s output before HIGH/LOW pairing (unless
  `include_deprecated`), so no candidate group contains a deprecated concept.

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/lifecycle.py` | Create | Canonical leaf: `deprecated_concept_ids(bundle_dir)` + generic `filter_hits`; `okf` + stdlib only |
| `src/openkos/retrieval/answer.py` | Modify | Add `include_deprecated: bool = False`; compute set once, filter `hits`/`vec_hits` pre-initial-fuse and `graph_hits` post-PPR |
| `src/openkos/resolution/contradiction.py` | Modify | Add `include_deprecated` param; drop candidate pairs touching a deprecated id |
| `src/openkos/resolution/candidates.py` | Modify | Add `include_deprecated` param; exclude deprecated ids from `find_candidates` before pairing |
| `src/openkos/cli/main.py` | Modify | Thread `--include-deprecated` flag on `query`, `contradictions`, `adjudicate` down to the library calls |
| `openspec/changes/status-aware-retrieval/specs/status-aware-retrieval/spec.md` | Exists | Approved spec (unchanged) |

No new ADR file is created for the reconcile capability — proposal defers
#1619; no reconcile-command spec is touched this slice.

## Interfaces / Contracts

```python
# src/openkos/lifecycle.py
def deprecated_concept_ids(bundle_dir: Path) -> frozenset[str]: ...
def filter_hits[H](hits: list[H], deprecated: frozenset[str]) -> list[H]: ...

# retrieval/answer.py
def answer(question, *, bundle_dir, llm, ..., include_deprecated: bool = False) -> AnswerResult: ...

# resolution/contradiction.py
def find_contradictions(bundle_dir, *, llm, include_deprecated: bool = False) -> tuple[list[ContradictionVerdict], int]: ...

# resolution/candidates.py
def find_candidates(bundle_dir, *, include_deprecated: bool = False) -> list[CandidateGroup]: ...
```

`include_deprecated=True` MUST short-circuit BEFORE the predicate walk (no
`_iter_docs` pass), so the escape flag is also the zero-cost / status-blind
path.

## Layering

`lifecycle.py` imports only `openkos.model.okf` (canonical → canonical). It is
consumed by `retrieval/answer.py` and `resolution/{contradiction,candidates}.py`
via `import openkos.lifecycle` — a package-root leaf both may depend on with no
cycle and no retrieval↔resolution coupling. It never imports `openkos.graph`,
`openkos.state`, or `openkos.config`.

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit (predicate) | status=deprecated alone → deprecated; superseded target → deprecated (own status irrelevant); self-`supersedes` → live; mutual `A↔B` → both live; malformed `relations` → no edges, no crash; all-live bundle → empty set | Temp bundle fixtures |
| Unit (`answer`) | deprecated absent from fts/vec/graph/fused/citations; only-deprecated-match → `zero_hits` NO_MATCH; `D→C` neighbor: C surfaces, D never a hit; `--include-deprecated` restores; all-live identical to pre-change | Fake retriever handles + fake LLM, spy on walk when flag set |
| Unit (contradiction) | superseded concept never in any candidate pair; flag restores; all-live identical | Fake LLM, temp bundle |
| Unit (adjudication) | deprecated excluded from candidate groups; flag restores | Temp bundle |
| CLI | `--include-deprecated` threads through `query`/`contradictions`/`adjudicate`; default excludes | Typer runner |

## Threat Matrix

N/A — read-only over local bundle files + injected LLM backend. No routing,
shell, subprocess, VCS/PR automation, or process-integration boundary. S1 is
UX/history-driven exclusion, NOT security fail-closed filtering (that is S3,
explicitly a separate slice).

## Migration / Rollout

No migration. No persisted schema change. Fully reversible: deleting
`lifecycle.py` and its call sites (or defaulting `include_deprecated=True`
everywhere) restores today's status-blind retrieval with no data migration —
`status`/`supersedes` remain written exactly as before.

## Risks (for tasks / verify to watch)

- **R1 — per-query walk cost.** The predicate reintroduces one `_iter_docs`
  pass into `answer()`, partially regressing Slice-5's no-per-query-build
  design (D4 caching). Deliberate: status lives only in frontmatter, and a
  single shared predicate across all five surfaces requires reading the live
  bundle. Mitigation deferred (future: persist the deprecated set into a
  derived index to restore the no-walk property) — out of scope for S1. When
  `include_deprecated=True`, the walk is skipped entirely, so the escape path
  has zero added cost.
- **R2 — cycles of length ≥ 3.** Reciprocal cancellation only guards the
  spec's self-reference and 2-cycle cases. A 3-cycle `A→B→C→A` (all
  `supersedes`) currently marks all three deprecated. The spec does not
  enumerate this; verify should confirm the chosen behavior is acceptable or
  pin an explicit rule (e.g. exclude any id that both supersedes and is
  superseded).
- **R3 — retrieval-summary counts.** Filtering hits/vec_hits early means
  `AnswerResult.fts_hit_count`/`dense_hit_count` and the `retrieval:` stderr
  line report POST-filter counts. This matches "deprecated absent from FTS
  hits," but tasks/verify should confirm the reporting semantics are intended
  and update the summary wording if needed.
- **R4 — index/predicate drift.** The predicate reads live frontmatter while
  retrievers read the last-reindexed snapshot. This is intentional (live
  status wins) but means a concept deprecated after the last `reindex` is
  correctly filtered even though it is still in `fts.db`; a concept deleted
  from disk but still in `fts.db` is not in the walk, so not filtered — a
  pre-existing staleness concern owned by `reindex`, not this slice.

## Open Questions

None blocking. R2 is the one interpretation the spec leaves genuinely open
beyond the 2-cycle guarantee; the default (reciprocal cancellation) satisfies
every enumerated scenario.
