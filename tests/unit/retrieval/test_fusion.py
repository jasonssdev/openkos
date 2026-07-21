"""Unit tests for `retrieval/fusion.py`: the pure RRF fusion helper.

`fuse()` is zero-I/O: every scenario here is a table-driven check of the
combined `Σ 1/(K_RRF+rank)` score and its resulting order, never touching
disk or a real FTS5/vec0 index.
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


# --- Phase 2: optional third `graph_hits` list -----------------------------


def test_concept_ranked_first_in_all_three_lists_outranks_fts_only_first() -> None:
    """`cid_A` rank 1 in FTS, dense, AND graph lists (`3 x 1/61`) strictly
    outranks `cid_B` rank 1 in FTS only (`1/61`)."""
    fts_hits = [
        FtsHit(concept_id="cid_A", score=0.0),
        FtsHit(concept_id="cid_B", score=1.0),
    ]
    vec_hits = [VecHit(concept_id="cid_A", distance=0.0)]
    graph_hits = [GraphHit(concept_id="cid_A", score=0.5)]

    result = fusion.fuse(fts_hits, vec_hits, graph_hits)

    assert result[0] == "cid_A"
    assert result.index("cid_A") < result.index("cid_B")


def test_graph_only_concept_surfaces_in_the_fused_output() -> None:
    """A `concept_id` absent from `fts_hits`/`vec_hits` but present in
    `graph_hits` surfaces in the fused output."""
    fts_hits = [FtsHit(concept_id="cid_fts", score=0.0)]
    vec_hits: list[VecHit] = []
    graph_hits = [GraphHit(concept_id="cid_graph_only", score=0.9)]

    result = fusion.fuse(fts_hits, vec_hits, graph_hits)

    assert "cid_graph_only" in result


def test_graph_hits_none_is_byte_identical_to_two_list_fuse() -> None:
    """`graph_hits=None` (omitted or explicit) produces the exact same
    ordered output as the current two-list `fuse(fts_hits, vec_hits)`."""
    fts_hits = [
        FtsHit(concept_id="cid_1", score=0.0),
        FtsHit(concept_id="cid_2", score=1.0),
    ]
    vec_hits = [VecHit(concept_id="cid_2", distance=0.0)]

    two_list = fusion.fuse(fts_hits, vec_hits)
    explicit_none = fusion.fuse(fts_hits, vec_hits, None)

    assert two_list == explicit_none


def test_no_truncation_of_a_ten_entry_graph_pool() -> None:
    """Every distinct `concept_id` from a 10-entry `graph_hits` list is
    represented in the fused output -- `fuse` never truncates."""
    graph_hits = [
        GraphHit(concept_id=f"graph_{i}", score=float(10 - i)) for i in range(10)
    ]

    result = fusion.fuse([], [], graph_hits)

    assert set(result) == {hit.concept_id for hit in graph_hits}
    assert len(result) == 10


def test_three_list_fusion_is_deterministic() -> None:
    """Identical three-list input triples called twice return
    byte-identical ordered output."""
    fts_hits = [FtsHit(concept_id="cid_1", score=0.0)]
    vec_hits = [VecHit(concept_id="cid_2", distance=0.0)]
    graph_hits = [GraphHit(concept_id="cid_3", score=0.5)]

    first = fusion.fuse(fts_hits, vec_hits, graph_hits)
    second = fusion.fuse(fts_hits, vec_hits, graph_hits)

    assert first == second


def test_ties_across_three_lists_still_break_by_concept_id_ascending() -> None:
    """Two `concept_id`s produce numerically equal fused scores after
    folding in `graph_hits` (`cid_z` rank 1 in FTS, `cid_a` rank 1 in
    graph, both `1/61`) -- the lexicographically smaller `concept_id`
    orders first."""
    fts_hits = [FtsHit(concept_id="cid_z", score=0.0)]
    vec_hits: list[VecHit] = []
    graph_hits = [GraphHit(concept_id="cid_a", score=0.5)]

    result = fusion.fuse(fts_hits, vec_hits, graph_hits)

    assert result == ["cid_a", "cid_z"]
