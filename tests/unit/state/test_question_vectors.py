"""Unit tests for `state/question_vectors.py`: the filed-question embedding cache.

The cache exists to make the near-duplicate scan cheap enough that it needs no
truncation. `evals/insight_scan_bound/` measured embedding a filed question at
~11.8 ms and comparing one at 0.053 ms -- 355x apart -- so caching the
embeddings turns a linear EMBED cost into a linear COSINE cost and removes the
reason #764's bound existed.

Rows are streamed rather than returned as a list: 5,000 filed insights is
5.1M floats, which is hundreds of megabytes as Python objects and nothing at
all one row at a time.
"""

import sqlite3
from pathlib import Path

import pytest

from openkos.state import question_vectors


def _open(tmp_path: Path) -> sqlite3.Connection:
    return question_vectors.open_question_vectors(tmp_path / ".openkos" / "q.db")


def test_a_stored_vector_comes_back_unchanged_enough(tmp_path: Path) -> None:
    """Round-tripping preserves the vector to float32 precision.

    Stored as float32 rather than float64: the threshold this feeds is 0.93
    and the loss is around 1e-7, while the size saving is half. The test
    pins APPROXIMATE equality on purpose -- asserting exact equality would
    be asserting the storage format is lossless, which it is not.
    """
    conn = _open(tmp_path)
    vector = [0.1, -0.25, 0.5, 1.0]
    question_vectors.store(
        conn, "bge-m3", [("insights/a", question_vectors.question_hash("q?"), vector)]
    )

    rows = list(question_vectors.iter_vectors(conn, "bge-m3"))

    assert len(rows) == 1
    concept_id, digest, stored = rows[0]
    assert concept_id == "insights/a"
    assert digest == question_vectors.question_hash("q?")
    assert stored == pytest.approx(vector, abs=1e-6)


def test_a_different_model_tag_yields_no_rows(tmp_path: Path) -> None:
    """Vectors from another embedding model are NOT cache hits.

    Two models put the same question in different spaces, so a cosine
    between them is meaningless rather than merely imprecise. The tag is
    part of the key, and a changed `embedding_model` must read as a total
    cache miss instead of silently comparing across spaces.
    """
    conn = _open(tmp_path)
    question_vectors.store(
        conn, "bge-m3", [("insights/a", question_vectors.question_hash("q?"), [1.0])]
    )

    assert list(question_vectors.iter_vectors(conn, "other-model")) == []


def test_restoring_the_same_id_replaces_rather_than_duplicates(
    tmp_path: Path,
) -> None:
    """One row per insight per model -- an edited question replaces its vector.

    Without this the cache would grow a row per edit and the scan would
    compare one insight several times, disclosing it more than once.
    """
    conn = _open(tmp_path)
    first = question_vectors.question_hash("old?")
    second = question_vectors.question_hash("new?")
    question_vectors.store(conn, "bge-m3", [("insights/a", first, [1.0, 0.0])])
    question_vectors.store(conn, "bge-m3", [("insights/a", second, [0.0, 1.0])])

    rows = list(question_vectors.iter_vectors(conn, "bge-m3"))

    assert len(rows) == 1
    assert rows[0][1] == second
    assert rows[0][2] == pytest.approx([0.0, 1.0], abs=1e-6)


def test_the_hash_tracks_the_question_text(tmp_path: Path) -> None:
    """A changed question must not be served from the old vector.

    The hash is what the caller compares to decide a re-embed, so it has to
    move when the text moves and stay put when it does not."""
    assert question_vectors.question_hash("a?") == question_vectors.question_hash("a?")
    assert question_vectors.question_hash("a?") != question_vectors.question_hash("b?")


def test_pruning_drops_only_the_ids_not_kept(tmp_path: Path) -> None:
    """Insights deleted from the bundle leave the cache.

    A filed insight can be removed or renamed; its vector would otherwise
    be compared forever against questions whose document no longer exists,
    and the scan would disclose a duplicate the operator cannot open.
    """
    conn = _open(tmp_path)
    digest = question_vectors.question_hash("q?")
    question_vectors.store(
        conn,
        "bge-m3",
        [("insights/a", digest, [1.0]), ("insights/b", digest, [1.0])],
    )

    question_vectors.prune_missing(conn, {"insights/a"})

    assert [row[0] for row in question_vectors.iter_vectors(conn, "bge-m3")] == [
        "insights/a"
    ]


def test_pruning_everything_empties_the_cache(tmp_path: Path) -> None:
    """An empty keep-set is a real instruction, never a no-op guard.

    A `prune_missing(conn, set())` that quietly kept every row would leave a
    cache for a bundle with no insights at all -- and it is the shape a
    caller reaches by passing an empty scan result.
    """
    conn = _open(tmp_path)
    question_vectors.store(
        conn, "bge-m3", [("insights/a", question_vectors.question_hash("q?"), [1.0])]
    )

    question_vectors.prune_missing(conn, set())

    assert list(question_vectors.iter_vectors(conn, "bge-m3")) == []


def test_pruning_survives_more_keys_than_sqlite_binds(tmp_path: Path) -> None:
    """A keep-set larger than `SQLITE_LIMIT_VARIABLE_NUMBER` must not raise.

    The retired implementation deleted with `WHERE concept_id NOT IN
    (?,?,...)` over the KEPT ids, binding one SQL variable per filed insight.
    That dies with `too many SQL variables` past the runtime limit -- 32,766
    on the SQLite measured here and 999 on builds carrying the older default
    -- and it lands on the write path of exactly the large bundles the cache
    exists to serve. Two review lenses found it independently.

    Deliberately built with ONE cached row and a huge keep-set rather than
    tens of thousands of rows: the old failure was driven by the size of
    `keep`, so this reproduces it in milliseconds. `_DELETE_BATCH` bounds the
    variables per statement, so no bundle size can reach the limit.
    """
    conn = _open(tmp_path)
    question_vectors.store(
        conn, "bge-m3", [("insights/a", question_vectors.question_hash("q?"), [1.0])]
    )
    limit = conn.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)
    keep = {f"insights/{index}" for index in range(limit + 10)} | {"insights/a"}

    question_vectors.prune_missing(conn, keep)

    assert [row[0] for row in question_vectors.iter_vectors(conn, "bge-m3")] == [
        "insights/a"
    ]


def test_pruning_deletes_more_rows_than_sqlite_binds(tmp_path: Path) -> None:
    """The DELETE side is batched too, not just the keep side.

    Fixing only the keep side would move the same failure onto a bundle whose
    insights were deleted en masse -- a `purge`, a reorganisation -- where the
    doomed set is what exceeds the limit.
    """
    conn = _open(tmp_path)
    digest = question_vectors.question_hash("q?")
    limit = conn.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)
    doomed = min(limit + 10, 3000)
    question_vectors.store(
        conn,
        "bge-m3",
        [(f"insights/{index}", digest, [1.0]) for index in range(doomed)],
    )

    question_vectors.prune_missing(conn, set())

    assert list(question_vectors.iter_vectors(conn, "bge-m3")) == []
