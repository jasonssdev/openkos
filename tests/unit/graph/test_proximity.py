"""Unit tests for `graph/proximity.py` -- the candidate-edge source (#183).

Every test drives a FAKE `NeighborQuery` returning fixed `VecHit` lists: no
Ollama, no sqlite-vec, no `vectors.db`. The real k-NN round trip is pinned
separately by `tests/unit/state/test_vectorstore.py`'s `neighbors` tests, and
the embedding-model assumptions by
`tests/unit/llm/test_ollama_embed_norm.py`. What is under test here is the
POLICY layer: the similarity floor, the top-K cap, self-exclusion, symmetry
collapse, and the promise never to raise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openkos.graph import proximity
from openkos.llm.base import EMBED_DIM
from openkos.state import vectorstore
from openkos.state.vectorstore import VecHit
from openkos.state.vectorstore import VecUnavailable as proximity_vec_unavailable


class _FakeNeighborQuery:
    """A `NeighborQuery` returning canned hits per concept id.

    Records the `k` it was asked for so tests can prove the anchor's own row
    is budgeted for, and honors `k` by truncating like the real vec0 query
    does."""

    def __init__(self, hits: dict[str, list[VecHit]]) -> None:
        self._hits = hits
        self.asked_k: list[int] = []

    def neighbors(self, concept_id: str, k: int) -> list[VecHit]:
        self.asked_k.append(k)
        return self._hits.get(concept_id, [])[:k]


class _ExplodingNeighborQuery:
    """A `NeighborQuery` whose k-NN always fails, standing in for a corrupt
    or unreadable `vectors.db` discovered mid-iteration."""

    def neighbors(self, concept_id: str, k: int) -> list[VecHit]:
        raise RuntimeError("vec0 query failed")


def _hit(concept_id: str, distance: float) -> VecHit:
    return VecHit(concept_id=concept_id, distance=distance)


# --- similarity floor -------------------------------------------------------


def test_pairs_includes_a_neighbor_exactly_at_the_distance_ceiling() -> None:
    """The floor is inclusive: a neighbor whose distance equals
    `MAX_NEIGHBOR_DISTANCE` has cosine EXACTLY
    `CANDIDATE_SIMILARITY_THRESHOLD`, which the constant defines as
    acceptable. An exclusive comparison here would make the documented
    threshold silently mean "strictly better than"."""
    query = _FakeNeighborQuery(
        {
            "concepts/a": [
                _hit("concepts/a", 0.0),
                _hit("concepts/b", proximity.MAX_NEIGHBOR_DISTANCE),
            ]
        }
    )

    pairs = proximity.VectorProximitySource(query).pairs(["concepts/a", "concepts/b"])

    assert [(p.source_id, p.target_id) for p in pairs] == [("concepts/a", "concepts/b")]


def test_pairs_drops_a_neighbor_just_beyond_the_distance_ceiling() -> None:
    """A neighbor fractionally past the ceiling is noise by definition and
    must not become an edge -- this is the assertion that keeps the graph
    from filling with weak links if the constant is ever loosened by
    accident."""
    query = _FakeNeighborQuery(
        {
            "concepts/a": [
                _hit("concepts/a", 0.0),
                _hit("concepts/b", proximity.MAX_NEIGHBOR_DISTANCE + 1e-9),
            ]
        }
    )

    pairs = proximity.VectorProximitySource(query).pairs(["concepts/a", "concepts/b"])

    assert pairs == []


# --- top-K cap --------------------------------------------------------------


def test_pairs_caps_each_anchor_at_top_k_nearest_neighbors() -> None:
    """`TOP_K` bounds how many candidates ONE concept can contribute, so a
    hub concept close to everything cannot flood the graph. The nearest are
    the ones kept."""
    query = _FakeNeighborQuery(
        {
            "concepts/hub": [_hit("concepts/hub", 0.0)]
            + [
                _hit(f"concepts/n{i}", 0.10 + i * 0.01)
                for i in range(proximity.TOP_K + 3)
            ]
        }
    )
    ids = ["concepts/hub"] + [f"concepts/n{i}" for i in range(proximity.TOP_K + 3)]

    pairs = proximity.VectorProximitySource(query).pairs(ids)

    assert len(pairs) == proximity.TOP_K
    assert [p.target_id for p in pairs] == [
        f"concepts/n{i}" for i in range(proximity.TOP_K)
    ]


def test_pairs_asks_for_one_more_than_top_k_to_budget_for_the_anchor() -> None:
    """`VectorStoreDB.neighbors` includes the anchor in its own result set
    (documented there, deliberately), so asking for exactly `TOP_K` would
    yield only `TOP_K - 1` usable candidates. The source must budget for
    it."""
    query = _FakeNeighborQuery({"concepts/a": [_hit("concepts/a", 0.0)]})

    proximity.VectorProximitySource(query).pairs(["concepts/a"])

    assert query.asked_k == [proximity.TOP_K + 1]


# --- self-exclusion ---------------------------------------------------------


def test_pairs_excludes_the_anchor_from_its_own_neighbors() -> None:
    """A concept is trivially nearest to itself at distance 0. A self-edge
    is meaningless in the graph, so this layer -- not the store -- drops
    it."""
    query = _FakeNeighborQuery({"concepts/a": [_hit("concepts/a", 0.0)]})

    pairs = proximity.VectorProximitySource(query).pairs(["concepts/a"])

    assert pairs == []


# --- symmetry collapse ------------------------------------------------------


def test_pairs_collapses_a_mutually_near_pair_into_one_canonical_row() -> None:
    """k-NN is near-symmetric: if `a` is close to `b`, `b` is close to `a`,
    so both anchors surface the same relationship. Emitting both directions
    would double every suggestion the user is asked to review. One row per
    unordered pair, canonicalized to `(min, max)`."""
    query = _FakeNeighborQuery(
        {
            "concepts/b": [_hit("concepts/b", 0.0), _hit("concepts/a", 0.2)],
            "concepts/a": [_hit("concepts/a", 0.0), _hit("concepts/b", 0.2)],
        }
    )

    pairs = proximity.VectorProximitySource(query).pairs(["concepts/b", "concepts/a"])

    assert [(p.source_id, p.target_id) for p in pairs] == [("concepts/a", "concepts/b")]


def test_pairs_are_returned_in_deterministic_sorted_order() -> None:
    """Pass 3 inserts these rows into the graph projection; a differing
    order between two runs over identical inputs would make the projection
    non-reproducible."""
    query = _FakeNeighborQuery(
        {
            "concepts/c": [_hit("concepts/c", 0.0), _hit("concepts/a", 0.2)],
            "concepts/a": [_hit("concepts/a", 0.0), _hit("concepts/b", 0.1)],
            "concepts/b": [_hit("concepts/b", 0.0)],
        }
    )
    ids = ["concepts/c", "concepts/a", "concepts/b"]

    first = proximity.VectorProximitySource(query).pairs(ids)
    second = proximity.VectorProximitySource(query).pairs(list(reversed(ids)))

    assert [(p.source_id, p.target_id) for p in first] == [
        ("concepts/a", "concepts/b"),
        ("concepts/a", "concepts/c"),
    ]
    assert first == second


# --- degradation ------------------------------------------------------------


def test_pairs_returns_empty_for_no_concepts() -> None:
    """An empty bundle yields no candidates and no query calls."""
    query = _FakeNeighborQuery({})

    assert proximity.VectorProximitySource(query).pairs([]) == []
    assert query.asked_k == []


def test_pairs_never_raises_when_the_underlying_knn_fails() -> None:
    """A k-NN failure degrades to zero candidates rather than aborting the
    whole `build_graph` -- candidate edges are an enhancement, and losing
    them must never cost the user their pass-1/pass-2 graph."""
    assert (
        proximity.VectorProximitySource(_ExplodingNeighborQuery()).pairs(["concepts/a"])
        == []
    )


def test_open_proximity_source_returns_none_when_vectors_db_is_absent(
    tmp_path: Path,
) -> None:
    """Existence-gated: no `vectors.db` means no candidate source, which
    `build_graph` treats as `candidates=None` -- a successful build with
    zero candidate rows, not an error."""
    assert proximity.open_proximity_source(tmp_path / "vectors.db") is None


def test_proximity_source_closes_the_store_it_was_handed(tmp_path: Path) -> None:
    """`open_proximity_source` opens a real SQLite connection, so something
    must close it. The source owns that lifecycle and exposes it as a
    context manager, mirroring `open_vector_store`'s own protocol -- a
    long-lived CLI process opening one per `build_graph` would otherwise
    leak a file handle per call."""

    class _ClosableQuery:
        def __init__(self) -> None:
            self.closed = False

        def neighbors(self, concept_id: str, k: int) -> list[VecHit]:
            return []

        def close(self) -> None:
            self.closed = True

    query = _ClosableQuery()
    with proximity.VectorProximitySource(query) as source:
        assert source.pairs([]) == []
    assert query.closed is True


def test_proximity_source_close_is_a_no_op_for_a_store_without_close() -> None:
    """A fake `NeighborQuery` satisfies the Protocol without being closable.
    Closing the source must not require it -- the Protocol deliberately
    declares only `neighbors`."""
    source = proximity.VectorProximitySource(_FakeNeighborQuery({}))

    source.close()  # must not raise


def test_top_k_cap_holds_even_if_the_store_ignores_k() -> None:
    """`TOP_K` is enforced HERE, not delegated to the store honoring `k`.
    A store that over-returns -- a different backend, a future vec0 -- must
    not be able to widen the cap."""

    class _OverReturningQuery:
        def neighbors(self, concept_id: str, k: int) -> list[VecHit]:
            del k  # deliberately ignored
            return [_hit(concept_id, 0.0)] + [
                _hit(f"concepts/n{i}", 0.1) for i in range(proximity.TOP_K + 10)
            ]

    pairs = proximity.VectorProximitySource(_OverReturningQuery()).pairs(
        ["concepts/anchor"]
    )

    assert len(pairs) == proximity.TOP_K


def test_close_swallows_a_failing_underlying_close() -> None:
    """Teardown must never be what breaks a build: a store whose `close`
    raises is reported as closed anyway, exactly as a failing k-NN degrades
    to zero candidates."""

    class _FailingCloseQuery:
        def neighbors(self, concept_id: str, k: int) -> list[VecHit]:
            return []

        def close(self) -> None:
            raise OSError("disk went away")

    source = proximity.VectorProximitySource(_FailingCloseQuery())

    source.close()  # must not raise


def test_open_proximity_source_returns_none_for_a_malformed_vectors_db(
    tmp_path: Path,
) -> None:
    """A file that exists but is not a usable vector store degrades to
    `None` rather than raising -- `build_graph` then behaves exactly as it
    does with no `vectors.db` at all."""
    db_path = tmp_path / "vectors.db"
    db_path.write_bytes(b"not a sqlite database")

    assert proximity.open_proximity_source(db_path) is None


@pytest.mark.skipif(
    not vectorstore.probe_vec_loadable(), reason="sqlite-vec extension not loadable"
)
def test_open_proximity_source_yields_a_working_source_over_a_real_store(
    tmp_path: Path,
) -> None:
    """End-to-end over the REAL store: `open_proximity_source` returns a
    source whose `pairs` runs a genuine vec0 k-NN and whose context manager
    closes the connection. This is the only test that proves
    `VectorStoreDB` actually satisfies `NeighborQuery` structurally -- every
    other test here uses a fake."""
    db_path = tmp_path / ".openkos" / "vectors.db"
    with vectorstore.open_vector_store(db_path) as db:
        db.upsert("concepts/a", [1.0] + [0.0] * (EMBED_DIM - 1), "h-a")
        db.upsert("concepts/b", [0.99, 0.01] + [0.0] * (EMBED_DIM - 2), "h-b")
        db.upsert("concepts/far", [0.0] * (EMBED_DIM - 1) + [1.0], "h-far")

    source = proximity.open_proximity_source(db_path)

    assert source is not None
    with source:
        pairs = source.pairs(["concepts/a", "concepts/b", "concepts/far"])

    assert [(p.source_id, p.target_id) for p in pairs] == [("concepts/a", "concepts/b")]


def test_open_proximity_source_returns_none_for_a_present_but_empty_store(
    tmp_path: Path,
) -> None:
    """ "Empty" means the same thing here as everywhere else in this feature:
    `state/vectorstore.py::vector_store_is_empty` -- absent OR present with
    zero embedded concepts.

    `cli/main.py` keys its "embeddings missing" state on exactly that
    predicate, so a source that reported itself AVAILABLE for a zero-row
    store would leave the two halves of the same feature disagreeing: the
    CLI telling the user embeddings are missing while pass 3 ran anyway."""
    db_path = tmp_path / ".openkos" / "vectors.db"
    with vectorstore.open_vector_store(db_path):
        pass  # creates the schema, embeds nothing

    assert db_path.exists()
    assert proximity.open_proximity_source(db_path) is None


def test_open_proximity_source_returns_none_when_opening_the_store_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard around `open_vector_store` is not dead code.

    `vector_store_is_empty` runs over a plain read-only connection that
    never loads `sqlite-vec`, so a store it reports as NON-empty can still
    fail to open as a real vector store -- the extension missing on this
    interpreter is the obvious case, a permission change between the two
    opens the narrower one. Either way the caller gets `None` and
    `build_graph` degrades, rather than an exception escaping into a CLI
    command that was only trying to read."""
    db_path = tmp_path / ".openkos" / "vectors.db"
    monkeypatch.setattr(proximity, "vector_store_is_empty", lambda path: False)

    def _explode(path: Path) -> object:
        raise proximity_vec_unavailable("sqlite-vec is not loadable here")

    monkeypatch.setattr(proximity, "open_vector_store", _explode)

    assert proximity.open_proximity_source(db_path) is None
