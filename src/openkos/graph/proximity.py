"""Embedding-proximity candidate source for the graph projection (#183).

`ingest` produces edges, but every one of them is a `derived_from`
Concept->Source provenance mirror, so the graph holds zero
concept-to-concept edges until a human runs `relate`. Both
`suggest-relations` and `contradictions` consume concept-to-concept edges,
so both starve. This module supplies the missing input: pairs of concepts
whose embeddings sit close enough to be worth ASKING a human about.

What this module is NOT: it does not assert that two concepts are related.
It nominates candidates. `relation_type` stays `NULL` on every row pass 3
writes, `suggest-relations` still asks an LLM to propose a type, and
`relate` still requires a human to accept it. Proximity opens the
conversation; it never concludes it.

Candidates are PROJECTION-EPHEMERAL. Nothing here is written to the bundle:
pairs are recomputed on every `build_graph`, exactly as `derived_from`
mirrors already are. Changing a constant below therefore changes behavior on
the next build with no migration and no bundle schema change.

## The constants

`VectorStore.query` returns ASCENDING vec0 L2 distance, not similarity. On
the unit sphere -- and `/api/embed` vectors ARE unit vectors, pinned by
`tests/unit/llm/test_ollama_embed_norm.py` to within 3.5e-07 -- the two are
interchangeable via `cosine = 1 - d^2 / 2`. The threshold is declared as a
SIMILARITY floor because that is the number a human can reason about, and
converted to a distance ceiling exactly once, here.

`CANDIDATE_SIMILARITY_THRESHOLD = 0.70` was calibrated against `bge-m3` over
FULL OKF concept documents -- the shape `state/reindex.py` actually embeds
(whole file text, frontmatter included), NOT bare titles. Measured on that
shape, topically-related pairs scored 0.7614-0.8018 and unrelated pairs
0.3837-0.6460; 0.70 sits essentially at the midpoint of that gap. The anchor
pair holding the floor honest is `Stoicism` / `Stoic Ethics` (related) against
`Medieval Crop Rotation` (unrelated), mirroring `resolution/similarity.py`'s
`stoic`/`stoicism` lexical lock one layer down.

Calibrating on bare titles instead gives a materially different and WRONG
distribution -- it suggested a floor near 0.45, which would fill the graph
with noise. If these constants are ever revisited, re-measure on full
documents.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import sqrt
from pathlib import Path
from typing import Final, Protocol

from openkos.state.vectorstore import (
    VecHit,
    open_vector_store,
    vector_store_is_empty,
)

CANDIDATE_SIMILARITY_THRESHOLD: Final[float] = 0.70
"""Cosine floor a pair must reach to be nominated. See the module docstring
for the calibration that produced it."""

MAX_NEIGHBOR_DISTANCE: Final[float] = sqrt(2 - 2 * CANDIDATE_SIMILARITY_THRESHOLD)
"""`CANDIDATE_SIMILARITY_THRESHOLD` expressed as the vec0 L2 distance
ceiling. Valid ONLY because embeddings are L2-normalized."""

TOP_K: Final[int] = 5
"""Most candidates ONE concept may contribute. Bounds the blast radius of a
hub concept that sits near everything, so the review queue a user faces
stays proportional to the bundle rather than to its densest node."""


class NeighborQuery(Protocol):
    """The one capability this module needs from a vector store.

    Declared here rather than imported so the dependency points inward:
    `VectorStoreDB` satisfies it structurally without knowing this module
    exists, and tests can substitute a fake with no `vectors.db` at all.
    Deliberately narrower than `state.vectorstore.VectorStore`, which no
    fake in the suite could satisfy for this purpose."""

    def neighbors(self, concept_id: str, k: int) -> list[VecHit]:
        """Return up to `k` hits nearest `concept_id`'s stored embedding,
        ascending by distance, INCLUDING `concept_id` itself."""
        ...


@dataclass(frozen=True)
class ProximityPair:
    """One nominated concept pair, canonically ordered.

    `source_id < target_id` always holds: k-NN is near-symmetric, so both
    concepts surface the same relationship, and emitting both directions
    would double every suggestion a human is asked to review."""

    source_id: str
    target_id: str
    distance: float
    """vec0 L2 distance between the two embeddings -- lower is closer.
    Retained for observability and future ranking; pass 3 does not read
    it."""


class VectorProximitySource:
    """Turns a `NeighborQuery` into canonical candidate pairs.

    Never raises. A k-NN failure mid-iteration yields zero candidates rather
    than aborting the caller: candidate edges are an enhancement, and losing
    them must never cost a user the pass-1/pass-2 graph they would otherwise
    have gotten."""

    def __init__(self, query: NeighborQuery) -> None:
        self._query = query

    def pairs(self, concept_ids: Sequence[str]) -> list[ProximityPair]:
        """Nominate candidate pairs among `concept_ids`, sorted.

        Order is deterministic and independent of `concept_ids`' order, so
        two builds over identical bundles produce byte-identical
        projections."""
        best: dict[tuple[str, str], float] = {}
        try:
            for concept_id in concept_ids:
                # +1 budgets for the anchor's own row, which `neighbors`
                # includes by design.
                hits = self._query.neighbors(concept_id, TOP_K + 1)
                kept = 0
                for hit in hits:
                    if kept >= TOP_K:
                        break
                    if hit.concept_id == concept_id:
                        continue
                    if hit.distance > MAX_NEIGHBOR_DISTANCE:
                        continue
                    kept += 1
                    key = (
                        min(concept_id, hit.concept_id),
                        max(concept_id, hit.concept_id),
                    )
                    # Both anchors may nominate the same pair; keep the
                    # smaller distance so the retained value does not depend
                    # on iteration order.
                    if key not in best or hit.distance < best[key]:
                        best[key] = hit.distance
        except Exception:
            return []
        return [
            ProximityPair(
                source_id=source, target_id=target, distance=best[(source, target)]
            )
            for source, target in sorted(best)
        ]

    def close(self) -> None:
        """Release the underlying store, if it holds anything to release.

        `open_proximity_source` hands over a real SQLite connection, so a
        long-lived CLI opening one per `build_graph` would otherwise leak a
        file handle per call. A fake `NeighborQuery` has nothing to close --
        the Protocol declares only `neighbors` -- so a missing `close` is
        not an error, and a failing one is swallowed for the same reason a
        failing k-NN is: teardown must never be what breaks a build."""
        closer = getattr(self._query, "close", None)
        if closer is None:
            return
        try:
            closer()
        except Exception:
            return

    def __enter__(self) -> VectorProximitySource:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def open_proximity_source(path: Path) -> VectorProximitySource | None:
    """Open `path` as a candidate source, or return `None` if it cannot
    serve one.

    Existence-gated and failure-tolerant: an absent, empty, unreadable or
    extension-less `vectors.db` all yield `None`, which `build_graph` treats
    as `candidates=None` -- a successful build with zero candidate rows, not
    an error. This is the seam that makes candidate edges degrade instead of
    break when embeddings have not been computed yet.

    "Empty" means what `state/vectorstore.py::vector_store_is_empty` means:
    absent, OR present with zero embedded concepts. `cli/main.py` keys its
    "embeddings missing" message on that same predicate, so reporting a
    zero-row store as AVAILABLE here would leave the two halves of this
    feature disagreeing -- the CLI telling a user embeddings are missing
    while pass 3 ran anyway."""
    if vector_store_is_empty(path):
        return None
    try:
        return VectorProximitySource(open_vector_store(path))
    except Exception:
        return None
