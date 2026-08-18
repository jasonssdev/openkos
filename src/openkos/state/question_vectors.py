"""Persisted embeddings of filed insights' SOURCE QUESTIONS (#764 follow-up).

## Why this exists

`resolution.insight_identity` compares a new `query --save` question against
the questions every already-filed insight was saved from. It used to embed
all of them on every save, which made the write path linear in the bundle:
`evals/insight_scan_bound/` measured ~11.8 ms per filed insight with no knee,
so a 400-insight bundle paid 4.8s of dead wait before its confirmation gate.

The bound that fixed the cost -- compare only the 100 most recently filed --
bought it with a loss NOTHING COULD MEASURE. Whether a duplicate survives
truncation depends on where it sits in FILING ORDER, which is a usage rate no
fixture produces, so the recall of the shipped feature was simply unknown and
had to be disclosed rather than trusted.

A stored question does not change, and neither does its embedding. Caching
them replaces the linear EMBED cost with a linear COSINE cost, and those are
measured ~220x apart: 11.8 ms to embed one question against 0.053 ms to
compare one. That is what lets the scan compare EVERYTHING again, which is
the only honest answer to "what does the bound cost" -- there is no bound.

## Shape, and why each part is the way it is

One row per `(concept_id, model_tag)`, carrying the question's hash and its
vector. The hash is what a caller compares to decide a re-embed; the model
tag is part of the key because two embedding models put one question in
different spaces, where a cosine is meaningless rather than merely imprecise.

Vectors are stored as **float32**. The consumer's threshold is 0.93 and the
precision lost is around 1e-7, against half the bytes. This is the one place
that trade is made, and it is why `iter_vectors` promises approximate, not
exact, round-tripping.

`iter_vectors` STREAMS. Five thousand filed insights is 5.1M floats, which is
hundreds of megabytes as Python float objects and nothing at all one row at a
time. A caller that materializes the iterator has undone the reason it is an
iterator.
"""

from __future__ import annotations

import hashlib
import sqlite3
from array import array
from collections.abc import Iterator, Sequence
from pathlib import Path

from openkos.state import derived

_CREATE_QUESTION_VECTORS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS question_vectors (
    concept_id TEXT NOT NULL,
    model_tag TEXT NOT NULL,
    question_hash TEXT NOT NULL,
    embedding BLOB NOT NULL,
    PRIMARY KEY (concept_id, model_tag)
)
"""

_UPSERT_SQL = (
    "INSERT INTO question_vectors "
    "(concept_id, model_tag, question_hash, embedding) VALUES (?, ?, ?, ?) "
    "ON CONFLICT(concept_id, model_tag) DO UPDATE SET "
    "question_hash = excluded.question_hash, embedding = excluded.embedding"
)

_SELECT_SQL = (
    "SELECT concept_id, question_hash, embedding FROM question_vectors "
    "WHERE model_tag = ? ORDER BY concept_id"
)

_DELETE_BATCH = 500
"""Ids per `DELETE` statement in `prune_missing`.

Bounds the bound variables per statement so no bundle size can reach
`SQLITE_LIMIT_VARIABLE_NUMBER` -- 999 on builds with the older default, and
32,766 on the one this was measured against. 500 sits under both with room to
spare, and the loop it feeds normally runs zero times."""

_TYPECODE = "f"
"""`array` typecode for float32 -- see the module docstring's precision note."""


def question_hash(question: str) -> str:
    """A stable digest of the question text a vector was computed from.

    Compared by the caller against the stored hash to decide whether a
    cached vector still describes the insight on disk. Hashing the TEXT
    rather than the file means an edit that leaves the question alone --
    a retitle, a body rewrite -- costs no re-embed."""
    return hashlib.sha256(question.encode("utf-8")).hexdigest()


def open_question_vectors(path: Path, *, connect: object = None) -> sqlite3.Connection:
    """Open (creating if needed) the question-vector cache at `path`.

    Delegates the lazy-create and no-new-footprint-on-failure contract to
    `derived.open_derived_connection` -- the same posture `findings.db`
    has -- then layers this store's own table on top. Re-opening an
    initialized database is a no-op migration.
    """
    conn = (
        derived.open_derived_connection(path)
        if connect is None
        else derived.open_derived_connection(path, connect=connect)  # type: ignore[arg-type]
    )
    try:
        conn.execute(_CREATE_QUESTION_VECTORS_TABLE_SQL)
        conn.commit()
    except BaseException:
        conn.close()
        raise
    return conn


def store(
    conn: sqlite3.Connection,
    model_tag: str,
    items: Sequence[tuple[str, str, Sequence[float]]],
) -> None:
    """Upsert `(concept_id, question_hash, vector)` rows for `model_tag`.

    UPSERT rather than insert: an insight whose question was edited must
    REPLACE its vector, not accumulate a second one, or the scan would
    compare that insight twice and disclose it twice."""
    conn.execute(_CREATE_QUESTION_VECTORS_TABLE_SQL)
    conn.executemany(
        _UPSERT_SQL,
        [
            (concept_id, model_tag, digest, array(_TYPECODE, vector).tobytes())
            for concept_id, digest, vector in items
        ],
    )
    conn.commit()


def iter_vectors(
    conn: sqlite3.Connection, model_tag: str
) -> Iterator[tuple[str, str, list[float]]]:
    """Stream `(concept_id, question_hash, vector)` for `model_tag`.

    Rows arrive one at a time and in `concept_id` order, so a caller can
    compare a whole bundle without ever holding it. Rows stored under a
    different model tag are not yielded -- they are not stale data to be
    fixed up, they are vectors in another space.
    """
    conn.execute(_CREATE_QUESTION_VECTORS_TABLE_SQL)
    for concept_id, digest, blob in conn.execute(_SELECT_SQL, (model_tag,)):
        vector = array(_TYPECODE)
        vector.frombytes(blob)
        yield str(concept_id), str(digest), [float(x) for x in vector]


def prune_missing(conn: sqlite3.Connection, keep: set[str]) -> None:
    """Delete every cached row whose `concept_id` is not in `keep`.

    An empty `keep` empties the cache, deliberately: it is the shape a
    caller reaches when the bundle has no comparable insights left, and a
    "guard" that treated it as a no-op would leave vectors for documents
    that no longer exist. Those would be compared forever and could
    disclose a duplicate the operator cannot open.

    Prunes across EVERY model tag, not just the current one: the row is
    dropped because its document is gone, which is true in every space.

    **Deletes by the ids GOING AWAY, never by the ids kept.** The obvious
    shape -- `WHERE concept_id NOT IN (?,?,...)` over `keep` -- binds one
    SQL variable per filed insight and dies with `too many SQL variables`
    once a bundle outgrows `SQLITE_LIMIT_VARIABLE_NUMBER`. That is 32,766 on
    the SQLite this was measured against and **999** on builds carrying the
    older default, and the failure lands on the write path of exactly the
    large bundles this cache exists to serve. Two review lenses found it
    independently.

    The set that actually leaves is normally EMPTY -- insights are deleted
    far more rarely than they are filed -- so this reads the cached ids
    (one column, no blobs), differences them in Python, and issues deletes
    only for what is really gone, in batches small enough that no bundle
    size can reach the limit.
    """
    conn.execute(_CREATE_QUESTION_VECTORS_TABLE_SQL)
    cached = {
        str(row[0])
        for row in conn.execute("SELECT DISTINCT concept_id FROM question_vectors")
    }
    doomed = sorted(cached - keep)
    if not doomed:
        conn.commit()
        return
    for index in range(0, len(doomed), _DELETE_BATCH):
        batch = doomed[index : index + _DELETE_BATCH]
        placeholders = ",".join("?" for _ in batch)
        # `placeholders` is a run of "?" built from the COUNT of ids in this
        # batch, which `_DELETE_BATCH` bounds; the ids themselves are bound
        # parameters and are never interpolated.
        sql = f"DELETE FROM question_vectors WHERE concept_id IN ({placeholders})"  # noqa: S608
        conn.execute(sql, tuple(batch))
    conn.commit()


class QuestionVectorStore:
    """A `conn` + `model_tag` bound into the shape the scan consumes.

    `resolution.insight_identity` declares a structural Protocol so it never
    owns a database connection; this is the on-disk implementation of it, and
    the CLI owns the lifetime. Binding the model tag here rather than passing
    it through every call is what keeps a caller from mixing two embedding
    spaces by forgetting an argument."""

    def __init__(self, conn: sqlite3.Connection, model_tag: str) -> None:
        self._conn = conn
        self._model_tag = model_tag

    def digest(self, question: str) -> str:
        return question_hash(question)

    def hashes(self) -> dict[str, str]:
        return {
            concept_id: digest
            for concept_id, digest, _ in iter_vectors(self._conn, self._model_tag)
        }

    def iter_vectors(self) -> Iterator[tuple[str, str, list[float]]]:
        return iter_vectors(self._conn, self._model_tag)

    def store(self, items: Sequence[tuple[str, str, Sequence[float]]]) -> None:
        store(self._conn, self._model_tag, items)

    def prune_missing(self, keep: set[str]) -> None:
        prune_missing(self._conn, keep)
