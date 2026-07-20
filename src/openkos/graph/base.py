"""The `GraphStore` seam: a node/edge/neighbor query Protocol and `Edge`.

This module is a leaf: stdlib `dataclasses`/`typing` only -- no import of
`networkx` (that conversion lives in the later `graph.analysis` module) and
no import of `openkos.model`, `openkos.bundle`, or `openkos.state`, mirroring
`openkos.llm.base`'s `LLMBackend` seam. Any concrete store (e.g. a
SQLite-backed projection) implements `GraphStore` structurally -- no
explicit inheritance required. Path finding is intentionally NOT part of
this Protocol; it is provided by NetworkX via `graph.analysis`, a later
slice.

Layering boundary: the canonical layer (`openkos.model`, `openkos.bundle`,
`openkos.state`) MUST NOT import `openkos.graph`.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Edge:
    """A directed edge between two OKF concept ids in the graph projection."""

    source_id: str
    """The OKF concept id (bundle-relative path, `.md` suffix removed) this
    edge originates from."""
    target_id: str
    """The OKF concept id this edge points to."""
    relation_type: str | None = None
    """Reserved for typed-edge work (`knowledge-object-model.md:211-222`);
    always `None` in this slice -- no vocabulary is committed yet."""


@runtime_checkable
class GraphStore(Protocol):
    """The derived-layer's node/edge/neighbor query surface over a projection.

    Any concrete store satisfies this Protocol structurally. Path finding is
    intentionally NOT part of this surface; it is provided by
    `graph.analysis`'s NetworkX conversion.
    """

    def nodes(self) -> list[str]:
        """Return every node id (OKF concept id) in the projection, sorted."""
        ...  # pragma: no cover -- Protocol stub body, never executed

    def edges(self) -> list[Edge]:
        """Return every edge in the projection, sorted and deterministic."""
        ...  # pragma: no cover -- Protocol stub body, never executed

    def neighbors(self, concept_id: str) -> list[str]:
        """Return the out-edge target node ids for `concept_id`."""
        ...  # pragma: no cover -- Protocol stub body, never executed
