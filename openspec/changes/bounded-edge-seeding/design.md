# Design: bounded-edge-seeding (issue #378)

## Technical Approach

Both requirements land **inside `_populate_graph_tables`'s pass 3**
(`graph/sqlite_graph.py:386-411`). `graph/proximity.py` is not touched: its purity
contract (`specs/candidate-edge-seeding/spec.md` R1) survives because the projection —
which already owns every doc's frontmatter — is the only layer that knows what a
`Source` is, and the ceiling is projection policy, not proximity policy.

Pass 3 gains three things, in order: a **seed node set** (Source-free), a **ranked
dedup map** that retains `distance`, and a **truncation report** returned to the caller.
Passes 1 and 2 are untouched, including the Concept→Source `derived_from` provenance
mirror.

## Architecture Decisions

### D1 — Source filter: seed set AND both endpoint guards

**Confirmed available.** `okf.load_frontmatter` yields the full metadata dict at
`sqlite_graph.py:339`, stored verbatim at `:347` (`metadatas.append((concept_id,
metadata))`). `type` is readable at pass 3 with **no second doc walk**.

**But narrowing the input list alone is insufficient.** `VectorProximitySource.pairs`
calls `self._query.neighbors(concept_id, TOP_K + 1)` (`proximity.py:126`) against the
*whole* `vectors.db` — hits are never filtered against the `concept_ids` argument. A
Source therefore still comes back as a *neighbor* even when it is not offered as an
*anchor*. The spec's "MUST NOT receive one" clause is only satisfied by filtering both
endpoints at row construction.

| Change | From | To |
|---|---|---|
| Anchor list | `candidates.pairs(sorted(node_ids))` (`:404`) | `candidates.pairs(sorted(seed_node_ids))` |
| Membership guards | `pair.source_id in node_ids and pair.target_id in node_ids` (`:405-406`) | same test against `seed_node_ids` |

```python
seed_node_ids = {cid for cid, metadata in metadatas if metadata.get("type") != "Source"}
```

**Predicate**: `type == "Source"` only — mirroring `resolution/candidates.py:98`'s
exclusion and the delta spec's exact wording. Deliberately **not** also excluding
missing/blank `type` (which `_iter_eligible` does): pass 1 and pass 2 already project
those docs as nodes, and dropping them from seeding would shrink the graph beyond spec.
`seed_node_ids ⊆ node_ids`, so the existing "is a real node" guarantee is preserved.

**Rejected**: `summary.py`'s `sources/`-prefix discriminator (`summary.py:32-36`). It
exists only because `summary` has no metadata and refuses a second walk; pass 3 holds
the authoritative `type` field. For a misfiled doc the two disagree, and pass 3's is
correct.

### D2 — Ceiling: `50`, confirmed

`_MAX_CANDIDATE_EDGES: Final[int] = 50`, private module constant in
**`graph/sqlite_graph.py`** — next to where it is enforced, exactly as
`contradiction._MAX_PAIRS` (`contradiction.py:71`) sits in the module that applies it,
and out of `proximity.py` to keep that module policy-free.

| Grounding | Number |
|---|---|
| Reported bundle, post-Source-filter | ~25 → 50 is 2× headroom; **zero fixture churn** (every pass-3 fixture uses 1-3 stub pairs) |
| Worst-case cost at 3-5 s/call | ≤50 typing calls ≈ 2.5-4 min, versus the observed 17m19s |
| Downstream backstop | `contradiction._MAX_PAIRS = 200` stays the cap on work *executed*; 50 caps what is *generated* |

Kept deliberately loose: shipping it changes no observed output, so it can be tightened
later on measurement with no behavioral regression to unwind.

### D3 — Rank before dedup collapse: `dict`, not `set`

The trap is real: `:402-409` collapses to a bare `set[tuple[str, str]]`, discarding
`distance`. Restructure to a `dict[tuple[str, str], float]` keyed by the canonical
`(min, max)` pair, keeping the smallest distance per pair (mirroring
`proximity.py:143-144`'s own tie rule). Order of operations, per
`contradiction.py:46-55`'s post-review HIGH correction:

    pairs() ─→ endpoint guard (D1) ─→ self-pair drop ─→ `seen` dedup vs passes 1+2
            ─→ best-distance collapse ─→ rank ─→ slice[:50] ─→ insert

```python
ranked = sorted(best, key=lambda key: (best[key], key))   # distance asc, id tie-break
retained = sorted(ranked[:_MAX_CANDIDATE_EDGES])          # insert in ID order
```

**Select by distance, insert by id.** Reads already go through `ORDER BY`
(`_SELECT_EDGES_SQL`), but the on-disk projection's byte identity depends on insertion
order — re-sorting the retained slice by id makes the under-cap case byte-identical to
today's `sorted(candidate_rows)` output. The `seen` set (`:395-401`) is unchanged and
still filled from both directions.

### D4 — Report channel: widen the return, do NOT use `skipped`

**Traced every consumer.** `store.skipped` has **zero production readers**: it is
written at `:328,331,336,341,378`, surfaced at `:242`, and read only by
`tests/unit/graph/test_sqlite_graph.py` (`:244,270,368,384,400,481,639`). No CLI verb
renders it, and `write_graph_store` discards the return entirely (`:485`). Riding it
would satisfy the spec's letter while telling the user nothing — and its `_skip_note`
shape (`{id}.md: skipped ({reason})`, `:219-221`) has no id to carry.

**Chosen**: a frozen `CandidateReport(produced: int, retained: int)` in
`sqlite_graph.py`, mirroring `contradiction`'s `(verdicts, total_pair_count)` shape.
`_populate_graph_tables` returns `tuple[list[str], CandidateReport]`;
`SqliteGraphStore.__init__` takes `candidate_report: CandidateReport | None = None`
(defaulted, so `open_graph_store_readonly` is untouched). `produced > retained` is the
cap-reached signal.

**Wording**, mirroring `curate.py:760-761` exactly:

    {retained} of {produced} candidate edge(s) shown (cap reached)

**Rendered by**: `suggest-relations` (`main.py:8088-8095`) and `contradictions`
(`main.py:8418`) — both already hold `store` inside a `with` block and already own
candidate-volume reporting — plus `curate`'s Structure gate via a new
`StageProbe.notice: str | None = None`, echoed in `gate()` immediately before
`cost_line` (`curate.py:212`). Curate matters because that is the verb the reported
17m19s failure was hit through.

### D6 — Slice boundary: clean, confirmed

D3's `set`→`dict` restructuring belongs to **slice 2**. Slice 1 changes only the anchor
list and the two membership guards; the set-comprehension shape survives untouched.

| Slice | `src/` files | `tests/` files |
|---|---|---|
| 1 — Source exclusion | `graph/sqlite_graph.py` | `tests/unit/graph/test_sqlite_graph.py` |
| 2 — rank + cap + report | `graph/sqlite_graph.py`, `cli/main.py`, `cli/curate.py` | `tests/unit/graph/test_sqlite_graph.py`, `tests/unit/cli/` (suggest-relations, contradictions, curate) |

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/graph/sqlite_graph.py` | Modify | `seed_node_ids` (S1); `_MAX_CANDIDATE_EDGES`, `CandidateReport`, dict-based ranked dedup, widened return (S2); module + function docstrings |
| `src/openkos/cli/main.py` | Modify (S2) | Truncation notice in `suggest-relations` and `contradictions` |
| `src/openkos/cli/curate.py` | Modify (S2) | `StageProbe.notice`, echoed in `gate()`; set by `_structure_probe` |
| `src/openkos/graph/proximity.py` | **Unchanged** | Purity contract preserved |
| `openspec/changes/bounded-edge-seeding/specs/graph-projection/spec.md` | Written | Both requirements |

## Interfaces / Contracts

```python
_MAX_CANDIDATE_EDGES: Final[int] = 50
"""Hard ceiling on candidate edges one build may emit. Bounds `curate`'s
one-LLM-call-per-untyped-edge run to ~2-4 minutes at 3-5s/call instead of the
17m19s a 74-candidate run cost (#378). Sits above the reported bundle's ~25
post-Source-filter volume (so today's output is unchanged) and below
`contradiction._MAX_PAIRS = 200`, which remains the downstream backstop on work
EXECUTED. Truncation is NEVER silent -- see `CandidateReport`."""

@dataclass(frozen=True)
class CandidateReport:
    produced: int = 0   # ranked, Source-excluded, deduped count BEFORE the cap
    retained: int = 0   # rows actually inserted
```

## Testing Strategy (strict TDD, RED first)

All in `tests/unit/graph/test_sqlite_graph.py` unless noted. `_StubCandidateSource`
(`:1433-1449`) exists and records `received` node-id lists; it is extended to accept
`(source, target, distance)` triples in slice 2. `_write_doc(path, doc_type=...)`
(`:35-46`) already writes arbitrary `type:` frontmatter — no new fixture helper needed.

### Slice 1 — RED

| Test | Asserts |
|---|---|
| `test_pass_three_excludes_source_docs_from_the_seed_node_set` | `stub.received == [["concepts/a", "concepts/b"]]` — no `sources/*` id was ever offered |
| `test_pass_three_drops_a_candidate_pair_originating_from_a_source` | stub returns `("sources/call", "concepts/a")`; zero candidate rows |
| `test_pass_three_drops_a_candidate_pair_targeting_a_source` | stub returns `("concepts/a", "sources/call")`; zero rows — **the guard-side case D1 proves is not covered by the anchor list** |
| `test_pass_three_still_seeds_concept_to_concept_pairs` | Concept↔Concept pair still inserted |
| `test_source_exclusion_leaves_the_derived_from_provenance_mirror_intact` | with `provenance: [sources/foo]` + `## Related` link, edge `→ sources/foo` still `"derived_from"` |

**Acceptance (i)**: no candidate row has an endpoint whose id starts `sources/`.

### Slice 2 — RED

| Test | Asserts |
|---|---|
| `test_pass_three_truncates_to_the_candidate_cap` | 60 stub pairs → exactly `_MAX_CANDIDATE_EDGES` rows |
| `test_pass_three_retains_the_closest_candidates_by_distance` | over-cap set with mixed distances → the retained ids are the smallest-distance ones |
| `test_pass_three_breaks_distance_ties_by_pair_id` | equal distances → lexicographic `(source, target)` selection |
| `test_pass_three_reports_the_pre_cap_total_when_truncating` | `store.candidate_report == CandidateReport(produced=60, retained=50)` |
| `test_pass_three_reports_no_truncation_under_the_cap` | `produced == retained`, notice suppressed |
| `test_dedup_against_earlier_passes_runs_before_the_cap` | a duplicate of a pass-1/pass-2 edge at the *closest* distance does not consume a cap slot |
| `test_under_cap_insertion_order_is_unchanged` | rows byte-identical to the pre-change build |
| `tests/unit/cli/` × 3 | `suggest-relations`, `contradictions`, `curate` each emit `"{n} of {m} candidate edge(s) shown (cap reached)"` when truncating, and nothing when not |

**Acceptance (ii)**: candidate rows ≤ cap at any bundle size, and the report carries the
correct pre-cap total.

### Regression — must pass UNEDITED

`tests/unit/graph/test_proximity.py` (all); pass-1/pass-2 tests
(`test_sqlite_graph.py:105-1428`); `test_pass_three_is_a_no_op_when_no_source_is_given`
(`:1467`); the `derived_from` provenance-mirror tests; the two-build determinism
assertions.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification,
or process-integration boundary. Pure in-process projection logic over an existing seam.

## Migration / Rollout

No migration. Candidate rows are projection-ephemeral — recomputed on every
`build_graph()`, never written to `relations:` frontmatter — so each slice is a
one-file `git revert` with no stale state. Feature Branch Chain: PR#1 → tracker,
PR#2 → PR#1.

## Open Questions

- [ ] `curate`'s `StageProbe.notice` plumbing assumes `gate()` (`curate.py:212`) is the
      only probe-consumption site that prints. If apply finds it needs more than ~20
      lines, defer curate rendering to a slice 3 rather than growing slice 2.
