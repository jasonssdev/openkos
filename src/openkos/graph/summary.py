"""A single read-only `(total, typed)` concept-to-concept edge count over
the graph projection, feeding the three-state message vocabulary Slice 0
wires into `status`/`suggest-relations`/`contradictions` (issue #183,
design.md's "Slice 0 -- three-state message vocabulary").

`graph_edge_summary` is a thin wrapper around `sqlite_graph.build_graph`: it
builds the SAME rebuild-per-run in-memory projection every other reader
uses, counts `edges()`, and closes the connection before returning -- never
mutates the bundle, never leaves a connection open, and degrades exactly
like `build_graph` does for unreadable/unparseable docs (skipped, not
crashed)."""

from pathlib import Path

from openkos.graph.base import Edge, GraphStore
from openkos.graph.sqlite_graph import build_graph
from openkos.model.types import CLASSIFIABLE_LINK_DIRS

# `nodes`/`edges` (`graph/sqlite_graph.py`) store only `concept_id` -- no
# per-node `type` column -- so there is no real type field to read here
# without re-parsing every endpoint's frontmatter (a second, independent
# doc-walk this read-only summary deliberately avoids). Every Concept-typed
# document is written under one of `CLASSIFIABLE_LINK_DIRS`
# (`openkos.model.types.REGISTRY`, the single source of truth for
# `type` -> directory), while `Source` is the sole registry entry NOT in
# that tuple (its own dir, `sources/`, is deliberately excluded). A node's
# first path segment is therefore a reliable, registry-derived discriminator
# -- not an arbitrary id-prefix guess.
_CONCEPT_DIR_PREFIXES = frozenset(CLASSIFIABLE_LINK_DIRS)


def _is_concept_node(node_id: str) -> bool:
    """Return whether `node_id` lives under a classifiable-type directory
    (a Concept-family node) rather than `sources/` (a Source node)."""
    prefix, _, _ = node_id.partition("/")
    return prefix in _CONCEPT_DIR_PREFIXES


def _is_concept_edge(edge: Edge) -> bool:
    """Return whether BOTH endpoints of `edge` are concept nodes -- the
    Concept<->Source `derived_from` provenance mirror (`okf.build_concept`'s
    `## Related` backlink) has a Source endpoint and must NOT count."""
    return _is_concept_node(edge.source_id) and _is_concept_node(edge.target_id)


def _summarize(store: GraphStore) -> tuple[int, int]:
    """Compute `(total, typed)` concept-to-concept edge counts over an
    already-open `store` (`graph_edge_summary`'s two-branch shape,
    graph-projection-reuse design §3): holds the two lines that used to live
    directly inside `graph_edge_summary`'s own `with build_graph(...)` block,
    so both branches (caller-supplied or self-built) share identical
    counting logic."""
    edges = [edge for edge in store.edges() if _is_concept_edge(edge)]
    total = len(edges)
    typed = sum(1 for edge in edges if edge.relation_type is not None)
    return total, typed


def graph_edge_summary(
    bundle_dir: Path, *, store: GraphStore | None = None
) -> tuple[int, int]:
    """Return `(total, typed)` CONCEPT-TO-CONCEPT edge counts for the graph
    projection over `bundle_dir`.

    `total` is every edge row whose source AND target are both Concept-family
    nodes (untyped-or-provenance-mirror plus typed, but NEVER a Concept<->
    Source `derived_from` link); `typed` is the subset of those whose
    `relation_type` is not `None`. Both are 0 for a bundle with no eligible
    docs or no concept-to-concept edges between them.

    `store` (graph-projection-reuse, issue #195): if the caller already has
    an open `GraphStore` (e.g. built once per CLI invocation and shared with
    another reader), pass it here and this function computes over it
    directly WITHOUT opening its own `build_graph` projection and WITHOUT
    ever closing the supplied store -- ownership stays with whoever opened
    it. `bundle_dir` is still required in that case (kept for signature
    symmetry with the `store=None` path and the zero-branch caller's
    context), even though it is not read again. Omitting `store` (the
    default) preserves today's behavior byte-for-byte: this function opens
    its own `with build_graph(bundle_dir) as store:` block and closes it
    before returning."""
    if store is not None:
        return _summarize(store)
    with build_graph(bundle_dir) as owned:
        return _summarize(owned)
