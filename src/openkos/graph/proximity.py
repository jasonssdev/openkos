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

`CANDIDATE_SIMILARITY_THRESHOLD` was ORIGINALLY calibrated at 0.70 against
`bge-m3` over full OKF concept documents -- whole file text, frontmatter
included -- the shape `state/reindex.py` embedded before #554. That is NOT
what `reindex()` embeds today: it composes `_compose_header` (title,
description, tags -- frontmatter is NEVER embedded) with one or more
`EMBED_COMPOSITION_TAG = "chunk-v1"` body chunks, and a document's stored
vector is the chunk mean, L2-renormalized --
`normalize(mean(normalize(chunk_i) for every chunk_i))` -- never a single
whole-file embedding (#888, #889).

The current value, 0.59, is the pre-registered calibration verdict for
that `chunk-v1` shape (#1052). The decision rule -- frozen in
`evals/proximity_threshold/DESIGN.md` before the fixture was scored and
applied mechanically by
`evals/proximity_threshold/run_proximity_threshold_probe.py`, which drives
the real `reindex()` path end to end rather than reimplementing
`_compose_header` or the chunk-mean derivation -- was run over a 91-pair
labelled fixture: 41 related pairs (21 hard: cross-lingual, cross-domain,
or linked by mechanism rather than vocabulary) and 50 unrelated pairs (34
hard same-domain distractors, 16 easy). The two classes OVERLAP on this
fixture: the weakest related pair scores 0.4238 and the strongest
unrelated pair scores 0.5827, so no floor separates them cleanly, and 0.70
already discarded most of the related signal (recall 0.317 -- 13 of 41
related pairs nominated). The rule found `t_min = 0.57`, the lowest grid
floor within the false-nomination budget (at most 1 of the 34 hard
negatives, `floor(0.05 * 34)`, and zero of the 16 easy negatives), then
added a 0.02 safety margin -- a hand-written negative set under-samples
the negative tail -- to land on `t* = 0.59`. At 0.59 the fixture shows
zero of 34 hard negatives and zero of 16 easy negatives nominated, while
recall across all 41 related pairs rises to 0.707 (29 of 41). Full
per-pair cosines and the floor/recall/false-nomination grid are in
`evals/proximity_threshold/results/proximity-threshold-20260930T065305Z-bge-m3.md`.

This is a trade-off, not a fix: proximity only NOMINATES a pair for a
human to review through `suggest-relations` / `relate`, so a false
positive costs review time and an LLM call -- and, because
`graph/sqlite_graph.py` keeps at most `_MAX_CANDIDATE_EDGES` candidates
per build ranked by distance, can crowd out a genuine candidate -- while a
false negative is a suggestion the user never sees, silent but
recoverable by hand. 0.59 was chosen because it was the floor at which the
fixture observed zero hard-negative false nominations while more than
doubling recall over 0.70; it does not claim the two classes are
separable, only that this floor's error trade is worth making on the
evidence measured. Moving `CANDIDATE_SIMILARITY_THRESHOLD` changes what
`suggest-relations` and `contradictions` see, which is its own design
discussion, not a docstring-correction side effect. The anchor pair
holding this measurement honest is still `Stoicism` / `Stoic Ethics`
(related, cosine 0.7997) against `Medieval Crop Rotation` (unrelated,
cosine 0.3558), mirroring `resolution/similarity.py`'s `stoic`/`stoicism`
lexical lock one layer down.

If these constants are ever revisited, re-run the harness above rather
than re-deriving a fixture from scratch -- and re-measure on whatever
`reindex()` embeds AT THAT TIME: this docstring is honest only about the
shape that produced the numbers it currently cites.
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

CANDIDATE_SIMILARITY_THRESHOLD: Final[float] = 0.59
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
        except Exception:  # noqa: BLE001 -- k-NN is advisory; any backend failure degrades to no candidates
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
        except Exception:  # noqa: BLE001 -- teardown must never be what breaks a build
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
    except Exception:  # noqa: BLE001 -- an unopenable vector store degrades to no candidates
        return None
