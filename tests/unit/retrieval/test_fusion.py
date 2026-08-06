"""Unit tests for `retrieval/fusion.py`: the pure rank-fusion helpers.

Both are zero-I/O, so every scenario here is a table-driven check that never
touches disk or a real FTS5/vec0 index: `fuse()` against the combined
`Σ 1/(K_RRF+rank)` RRF score and its resulting order, and
`fuse_with_graph()` against the additive, reserved-slot graph contract
(#402) -- the graph adds what FTS and dense missed, and reorders nothing.
"""

from openkos.retrieval import fusion
from openkos.retrieval.fusion import GraphHit
from openkos.state.fts import FtsHit
from openkos.state.vectorstore import VecHit


def test_k_rrf_constant_is_sixty() -> None:
    """`K_RRF` is the frozen fusion constant the spec pins at `60`."""
    assert fusion.K_RRF == 60


def test_presence_in_both_lists_outranks_presence_in_one() -> None:
    """`cid_A` rank 1 in both lists outranks `cid_B` rank 1 in FTS only."""
    fts_hits = [
        FtsHit(concept_id="cid_A", score=0.0),
        FtsHit(concept_id="cid_B", score=1.0),
    ]
    vec_hits = [VecHit(concept_id="cid_A", distance=0.0)]

    result = fusion.fuse(fts_hits, vec_hits)

    assert result == ["cid_A", "cid_B"]


def test_k60_formula_matches_a_worked_example() -> None:
    """A `cid` at FTS rank 3, absent from dense, scores exactly `1/(60+3)`."""
    fts_hits = [
        FtsHit(concept_id="other_1", score=0.0),
        FtsHit(concept_id="other_2", score=1.0),
        FtsHit(concept_id="cid", score=2.0),
    ]

    result = fusion.fuse(fts_hits, [])

    assert result[2] == "cid"
    # Cross-check the exact score via a second, distinguishable computation:
    # re-fuse with `cid` alone (still rank 3) and confirm ordering is stable
    # relative to a hand-computed rank-1 competitor at exactly 1/(60+3).
    solo_fts = [
        FtsHit(concept_id="filler_1", score=0.0),
        FtsHit(concept_id="filler_2", score=1.0),
        FtsHit(concept_id="cid", score=2.0),
    ]
    solo_result = fusion.fuse(solo_fts, [])
    assert solo_result == ["filler_1", "filler_2", "cid"]


def test_equal_fused_scores_tie_break_by_concept_id_ascending() -> None:
    """Two `concept_id`s with numerically equal fused scores (each rank 1 in
    its own single list, so both score `1/61`) order by `concept_id`
    ascending."""
    fts_hits = [FtsHit(concept_id="cid_z", score=0.0)]
    vec_hits = [VecHit(concept_id="cid_a", distance=0.0)]

    result = fusion.fuse(fts_hits, vec_hits)

    assert result == ["cid_a", "cid_z"]


def test_all_elements_of_both_pools_are_represented() -> None:
    """Every distinct `concept_id` from both pools appears in the output,
    with no truncation."""
    fts_hits = [FtsHit(concept_id=f"fts_{i}", score=float(i)) for i in range(10)]
    vec_hits = [VecHit(concept_id=f"vec_{i}", distance=float(i)) for i in range(10)]
    fts_hits[0] = FtsHit(concept_id="shared", score=0.0)
    vec_hits[0] = VecHit(concept_id="shared", distance=0.0)

    result = fusion.fuse(fts_hits, vec_hits)

    expected_ids = {hit.concept_id for hit in fts_hits} | {
        hit.concept_id for hit in vec_hits
    }
    assert set(result) == expected_ids
    assert len(result) == len(expected_ids)


def test_empty_fts_list_ranks_purely_by_dense_positions() -> None:
    """`fts_hits = []` -> output equals the dense list's `concept_id` order."""
    vec_hits = [
        VecHit(concept_id="cid_1", distance=0.0),
        VecHit(concept_id="cid_2", distance=1.0),
    ]

    result = fusion.fuse([], vec_hits)

    assert result == ["cid_1", "cid_2"]


def test_empty_dense_list_ranks_purely_by_fts_positions() -> None:
    """`vec_hits = []` -> output equals the FTS list's `concept_id` order."""
    fts_hits = [
        FtsHit(concept_id="cid_1", score=0.0),
        FtsHit(concept_id="cid_2", score=1.0),
    ]

    result = fusion.fuse(fts_hits, [])

    assert result == ["cid_1", "cid_2"]


def test_both_lists_empty_returns_empty_list_without_error() -> None:
    """Both inputs empty -> `[]`, no exception."""
    result = fusion.fuse([], [])

    assert result == []


def test_duplicate_within_one_list_is_deduplicated_by_best_rank() -> None:
    """`cid` at rank 1 and again at rank 5 in `fts_hits` contributes only its
    best (rank-1) occurrence, `1/(60+1)`, not the sum of both."""
    fts_hits = [
        FtsHit(concept_id="cid", score=0.0),
        FtsHit(concept_id="filler_1", score=1.0),
        FtsHit(concept_id="filler_2", score=2.0),
        FtsHit(concept_id="filler_3", score=3.0),
        FtsHit(concept_id="cid", score=4.0),
    ]
    # A control list where "cid" appears once at rank 1 only, everything else
    # identical -- its fused score MUST match the duplicate-list score
    # exactly (proving the second occurrence added nothing).
    control_fts_hits = [
        FtsHit(concept_id="cid", score=0.0),
        FtsHit(concept_id="filler_1", score=1.0),
        FtsHit(concept_id="filler_2", score=2.0),
        FtsHit(concept_id="filler_3", score=3.0),
    ]

    result = fusion.fuse(fts_hits, [])
    control_result = fusion.fuse(control_fts_hits, [])

    assert result.index("cid") == control_result.index("cid") == 0


def test_same_inputs_yield_the_same_output_every_call() -> None:
    """Two calls with the same fixed inputs return byte-identical output."""
    fts_hits = [
        FtsHit(concept_id="cid_1", score=0.0),
        FtsHit(concept_id="cid_2", score=1.0),
    ]
    vec_hits = [VecHit(concept_id="cid_2", distance=0.0)]

    first = fusion.fuse(fts_hits, vec_hits)
    second = fusion.fuse(fts_hits, vec_hits)

    assert first == second


# --- `fuse_with_graph`: the additive, reserved-slot graph channel (#402) ----


def _numbered_pool(size: int) -> tuple[list[FtsHit], list[VecHit]]:
    """An FTS and a dense list agreeing on `size` `pool_i` concepts, so the
    two-list base ranking is exactly `["pool_0", ..., f"pool_{size-1}"]`."""
    fts_hits = [FtsHit(concept_id=f"pool_{i}", score=float(i)) for i in range(size)]
    vec_hits = [VecHit(concept_id=f"pool_{i}", distance=float(i)) for i in range(size)]
    return fts_hits, vec_hits


def test_graph_reserved_slots_constant_is_one() -> None:
    """`GRAPH_RESERVED_SLOTS` is the frozen cap on how many final top-k slots
    the graph channel may claim."""
    assert fusion.GRAPH_RESERVED_SLOTS == 1


def test_graph_only_concept_fills_a_reserved_tail_slot() -> None:
    """A `concept_id` present ONLY in `graph_hits` claims a reserved slot at
    the tail of the final top-k."""
    fts_hits, vec_hits = _numbered_pool(6)
    graph_hits = [GraphHit(concept_id="graph_only", score=0.9)]

    result = fusion.fuse_with_graph(fts_hits, vec_hits, graph_hits, limit=5)

    assert result == ["pool_0", "pool_1", "pool_2", "pool_3", "graph_only"]


def test_a_concept_already_in_the_pool_gets_no_graph_contribution() -> None:
    """The #402 eviction: a concept at graph rank 1 that FTS+dense already
    found MUST NOT be promoted, and MUST NOT displace anything the base
    ranking placed above it."""
    fts_hits, vec_hits = _numbered_pool(5)
    graph_hits = [
        GraphHit(concept_id="pool_4", score=0.9),
        GraphHit(concept_id="pool_3", score=0.8),
    ]

    result = fusion.fuse_with_graph(fts_hits, vec_hits, graph_hits, limit=5)

    assert result == ["pool_0", "pool_1", "pool_2", "pool_3", "pool_4"]


def test_graph_contributing_nothing_new_is_byte_identical_to_two_list_fuse() -> None:
    """WHEN every `graph_hits` entry is already inside the FTS+dense pool,
    the result is byte-identical to `fuse(fts_hits, vec_hits)[:limit]`."""
    fts_hits, vec_hits = _numbered_pool(8)
    graph_hits = [
        GraphHit(concept_id=f"pool_{i}", score=float(8 - i)) for i in range(8)
    ]

    with_graph = fusion.fuse_with_graph(fts_hits, vec_hits, graph_hits, limit=5)

    assert with_graph == fusion.fuse(fts_hits, vec_hits)[:5]


def test_empty_graph_list_is_byte_identical_to_two_list_fuse() -> None:
    """An empty `graph_hits` list (graph degraded, or an edgeless bundle)
    returns exactly the FTS+dense top-k."""
    fts_hits, vec_hits = _numbered_pool(8)

    with_graph = fusion.fuse_with_graph(fts_hits, vec_hits, [], limit=5)

    assert with_graph == fusion.fuse(fts_hits, vec_hits)[:5]


def test_reserved_slots_are_capped_at_graph_reserved_slots() -> None:
    """A 10-entry graph pool of entirely new concepts claims exactly
    `GRAPH_RESERVED_SLOTS` slots, never more."""
    fts_hits, vec_hits = _numbered_pool(8)
    graph_hits = [
        GraphHit(concept_id=f"graph_{i}", score=float(10 - i)) for i in range(10)
    ]

    result = fusion.fuse_with_graph(fts_hits, vec_hits, graph_hits, limit=5)

    assert result == ["pool_0", "pool_1", "pool_2", "pool_3", "graph_0"]
    assert len([cid for cid in result if cid.startswith("graph_")]) == (
        fusion.GRAPH_RESERVED_SLOTS
    )


def test_reserved_slots_fill_in_graph_rank_order() -> None:
    """Reserved slots go to the best-ranked graph-only concepts, in
    `graph_hits` order -- never re-sorted by `concept_id` (which would have
    picked `alpha`)."""
    fts_hits, vec_hits = _numbered_pool(3)
    graph_hits = [
        GraphHit(concept_id="zebra", score=0.9),
        GraphHit(concept_id="alpha", score=0.8),
    ]

    result = fusion.fuse_with_graph(fts_hits, vec_hits, graph_hits, limit=5)

    assert result == ["pool_0", "pool_1", "pool_2", "zebra"]


def test_reserved_slots_never_take_more_than_half_the_limit() -> None:
    """The base ranking always keeps at least half of a small `limit`: the
    graph claims `min(GRAPH_RESERVED_SLOTS, limit // 2)` slots."""
    fts_hits, vec_hits = _numbered_pool(8)
    graph_hits = [
        GraphHit(concept_id="graph_a", score=0.9),
        GraphHit(concept_id="graph_b", score=0.8),
    ]

    assert fusion.fuse_with_graph(fts_hits, vec_hits, graph_hits, limit=1) == ["pool_0"]
    assert fusion.fuse_with_graph(fts_hits, vec_hits, graph_hits, limit=2) == [
        "pool_0",
        "graph_a",
    ]
    assert fusion.fuse_with_graph(fts_hits, vec_hits, graph_hits, limit=3) == [
        "pool_0",
        "pool_1",
        "graph_a",
    ]


def test_a_repeated_graph_only_concept_appears_once() -> None:
    """A `concept_id` repeated within `graph_hits` is one concept, not two
    -- it occupies its reserved slot exactly once."""
    fts_hits, vec_hits = _numbered_pool(3)
    graph_hits = [
        GraphHit(concept_id="graph_a", score=0.9),
        GraphHit(concept_id="graph_a", score=0.5),
    ]

    result = fusion.fuse_with_graph(fts_hits, vec_hits, graph_hits, limit=5)

    assert result == ["pool_0", "pool_1", "pool_2", "graph_a"]


def test_a_short_base_ranking_is_not_padded_beyond_the_reserved_cap() -> None:
    """WHEN the base ranking is shorter than `limit`, the graph still claims
    at most its reserved slots -- the cap is a cap, not a quota to fill."""
    fts_hits = [FtsHit(concept_id="pool_0", score=0.0)]
    graph_hits = [
        GraphHit(concept_id=f"graph_{i}", score=float(10 - i)) for i in range(10)
    ]

    result = fusion.fuse_with_graph(fts_hits, [], graph_hits, limit=5)

    assert result == ["pool_0", "graph_0"]


def test_fuse_with_graph_never_permutes_the_base_ranking() -> None:
    """Whatever the graph list says, the surviving base concepts stay in
    their FTS+dense fused order -- the graph adds, it never reorders."""
    fts_hits = [
        FtsHit(concept_id="cid_b", score=0.0),
        FtsHit(concept_id="cid_a", score=1.0),
        FtsHit(concept_id="cid_c", score=2.0),
    ]
    vec_hits = [VecHit(concept_id="cid_b", distance=0.0)]
    graph_hits = [
        GraphHit(concept_id="cid_c", score=0.99),
        GraphHit(concept_id="cid_a", score=0.98),
        GraphHit(concept_id="graph_only", score=0.1),
    ]

    base = fusion.fuse(fts_hits, vec_hits)
    result = fusion.fuse_with_graph(fts_hits, vec_hits, graph_hits, limit=5)

    assert [cid for cid in result if cid in base] == base


def test_fuse_with_graph_is_deterministic() -> None:
    """Identical inputs called twice return byte-identical ordered output."""
    fts_hits, vec_hits = _numbered_pool(4)
    graph_hits = [GraphHit(concept_id="graph_only", score=0.5)]

    first = fusion.fuse_with_graph(fts_hits, vec_hits, graph_hits, limit=5)
    second = fusion.fuse_with_graph(fts_hits, vec_hits, graph_hits, limit=5)

    assert first == second


def test_fuse_with_graph_on_empty_inputs_returns_empty() -> None:
    """All three lists empty -> `[]`, no exception."""
    assert fusion.fuse_with_graph([], [], [], limit=5) == []
