"""The #1648 Step 2 dense arms, scored from exact distances to every chunk row.

`VectorStore.query` collapses each document to its MINIMUM chunk distance inside
the store, so the other chunks' distances are not reachable through it. The arms
(`PREREGISTRATION-1648.md`) need them, so this module reads every chunk row of
the built `vectors.db` and measures the question against each, brute force (a
few hundred rows). The metric is `vec0`'s own (L2 on float vectors); the
self-test pins that the `baseline` arm reproduces `answer._dense_search`.

Arms: `baseline` = the shipped minimum; `t1` = mean of the two smallest chunk
distances; `t2` = minimum plus `lambda * log2(chunk_count)`. T3 (the 4,000
character chunk target) is not an aggregation: it is a different store, built
under `gold_prereg.chunk_target`. No file in `src/` changes.
"""

from __future__ import annotations

import math
from array import array
from collections.abc import Mapping, Sequence
from typing import Any, Final

import gold_prereg as pre

from openkos.state.vectorstore import VecHit

ARMS: Final = ("baseline", "t1", "t2")
T1_M: Final = 2
"""Registered: the mean of the two smallest chunk distances."""
T2_FRACTION: Final = 0.10
"""Registered: `lambda` is this share of the median baseline top-10 spread."""

_METHOD: Final = {"baseline": "min", "t1": "mean_top_m", "t2": "penalty"}


def decode_embedding(blob: bytes) -> list[float]:
    """The float32 vector `sqlite_vec.serialize_float32` wrote."""
    values = array("f")
    values.frombytes(blob)
    return list(values)


def l2(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError("vectors differ in length")
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=True)))


def read_chunk_rows(store: Any) -> tuple[dict[str, list[list[float]]], dict[str, int]]:
    """`({document: [chunk vector, ...]}, {document: chunk_count})` from an open
    vector store. Reads the connection directly: the `VectorStore` Protocol has
    no such accessor, and it is not worth adding one to `src/` for a harness."""
    conn = store._conn
    vectors: dict[str, list[list[float]]] = {}
    for doc, blob in conn.execute("SELECT concept_id, embedding FROM vectors"):
        vectors.setdefault(str(doc), []).append(decode_embedding(blob))
    chunks = {
        str(doc): int(n)
        for doc, n in conn.execute("SELECT concept_id, chunk_count FROM vector_meta")
    }
    return vectors, chunks


def chunk_distances(
    question: Sequence[float], vectors: Mapping[str, Sequence[Sequence[float]]]
) -> dict[str, list[float]]:
    """The distance of the question to EVERY chunk of every document."""
    return {doc: [l2(question, v) for v in rows] for doc, rows in vectors.items()}


def dense_hits(
    distances: Mapping[str, Sequence[float]],
    chunks: Mapping[str, int],
    arm: str,
    *,
    lam: float,
    depth: int,
) -> list[VecHit]:
    """The top-`depth` documents under `arm`, as the `VecHit`s the shipped
    fusion takes. Ties break by concept id, as in production."""
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; one of {ARMS}")
    scores = pre.score_docs(distances, chunks, _METHOD[arm], m=T1_M, lam=lam)
    ordered = sorted(scores.items(), key=lambda pair: (pair[1], pair[0]))[:depth]
    return [VecHit(concept_id=d, distance=s) for d, s in ordered]


def fit_lambda(
    per_question: Sequence[Mapping[str, Sequence[float]]],
    chunks: Mapping[str, int],
    depth: int,
) -> float:
    """The registered `lambda`: `T2_FRACTION` of the median, over questions, of
    the spread (last minus first) of the BASELINE top-`depth` list. Fixed from the
    baseline distances alone, before any metric is read."""
    tops = [
        [h.distance for h in dense_hits(d, chunks, "baseline", lam=0.0, depth=depth)]
        for d in per_question
    ]
    return pre.penalty_lambda(tops, T2_FRACTION)
