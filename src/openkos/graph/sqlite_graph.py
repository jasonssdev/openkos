"""In-memory SQLite node-edge projection over the compiled bundle.

The derived-layer counterpart to `state/fts.py`: `build_graph` mirrors
`build_index` EXACTLY -- a rebuild-per-run `sqlite3(":memory:")` connection,
a single `okf._iter_docs` pass, and a TOCTOU-guarded body re-read, with
unreadable/unparseable docs skipped and noted rather than crashing the build.
Nodes are OKF concept ids (bundle-relative path, `.md` suffix removed), one
per non-reserved doc `_iter_docs` yields -- the same identity `fts.py` and
`forget` use. Edges come from TWO independent passes over the same doc set,
inserted as separate rows even between the same `(source_id, target_id)`
pair:

1. UNTYPED, from `_LINK_RE`: a bundle-relative `[text](/….md)` markdown
   link in the doc body, with any `#anchor` stripped, `relation_type` always
   `NULL`. Edges to a target that does not resolve to a known node in the
   same projection (external, non-bundle-relative, non-`.md`, or dangling)
   are dropped silently -- the build never raises because of them. A doc
   body is fence-masked (`_mask_fenced_code_blocks`) before edge extraction,
   so a link inside a fenced code block (e.g. raw ingested source material
   embedded verbatim under `## Source content`, see
   `okf.build_source_concept`) never produces a spurious edge, while the
   same link in ordinary prose or `## Related` still resolves.
2. TYPED, from the doc's `relations:` frontmatter (`okf.decode_relations`):
   one edge per entry whose `target` resolves to a known node, carrying that
   entry's `type` as `relation_type`. A `relations:` entry whose `target`
   does not resolve is dropped silently -- the same drop-if-unresolvable
   rule the untyped pass already applies. A doc whose `relations:` fails to
   decode (malformed shape) contributes no typed edges rather than crashing
   the build, mirroring this module's existing degrade-not-crash posture.

Each pass dedupes its own rows before insert -- the untyped pass on
`(source_id, target_id)`, the typed pass on `(source_id, target_id,
relation_type)` -- and both are inserted in sorted order so a rebuild over
an unchanged bundle is deterministic. A typed edge and an untyped edge
between the same pair are DISTINCT rows: this dedup key is why a doc can
have both a `## Related` link AND a `relations:` entry pointing at the same
target without collapsing into one row.

Any exception during the build closes the in-memory connection before
propagating, so a failed build never leaks it -- only a successful build
hands the open connection off to the returned `SqliteGraphStore`.

Layering boundary: the canonical layer (`openkos.model`, `openkos.bundle`,
`openkos.state`) MUST NOT import `openkos.graph`.
"""

import re
import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Final

from openkos.graph.base import Edge
from openkos.model import okf

_LINK_RE: Final = re.compile(r"\[[^\]]*\]\(/([^)\s#]+\.md)(?:#[^)]*)?\)")
"""A bundle-relative `[text](/….md)` markdown link, per
`docs/knowledge-object-model.md`'s link shape (the same shape `okf.build_concept`
emits for `## Related` backlinks). The leading `/` requirement excludes
external URLs (`https://…`) and bare relative links (`concepts/x.md`); the
`\\.md` requirement excludes non-Markdown targets; an optional trailing
`#anchor` is matched but NOT captured, so it never becomes part of the target
concept id."""

_FENCE_MARKERS: Final = ("```", "~~~")


def _mask_fenced_code_blocks(body: str) -> str:
    """Blank out every line inside a fenced code block, keeping every other
    line (fence lines included) byte-identical so line/segment boundaries --
    and any non-fenced link elsewhere in the body -- are unaffected.

    A fence opens on a line whose first non-whitespace characters are ` ``` `
    or `~~~` and closes on the next line whose first non-whitespace
    characters are the SAME marker. Concept docs can embed raw ingested
    source material verbatim (`## Source content`, see
    `okf.build_source_concept`); if that material contains fenced code with
    example markdown-link syntax, it must not be mistaken for a real edge.
    This is a scoped regex-consistent mask, not full CommonMark parsing.
    """
    lines = body.split("\n")
    masked: list[str] = []
    fence_marker: str | None = None
    for line in lines:
        stripped = line.lstrip()
        opens_or_closes = stripped.startswith(_FENCE_MARKERS)
        if fence_marker is None:
            if opens_or_closes:
                fence_marker = stripped[:3]
                masked.append("")
            else:
                masked.append(line)
        else:
            if opens_or_closes and stripped[:3] == fence_marker:
                fence_marker = None
            masked.append("")
    return "\n".join(masked)


_CREATE_NODES_SQL = "CREATE TABLE nodes (concept_id TEXT PRIMARY KEY)"

_CREATE_EDGES_SQL = """
CREATE TABLE edges (
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation_type TEXT
)
"""

_CREATE_EDGES_SOURCE_INDEX_SQL = "CREATE INDEX idx_edges_source_id ON edges (source_id)"

_CREATE_EDGES_TARGET_INDEX_SQL = "CREATE INDEX idx_edges_target_id ON edges (target_id)"

_INSERT_NODE_SQL = "INSERT INTO nodes (concept_id) VALUES (?)"

_INSERT_EDGE_SQL = (
    "INSERT INTO edges (source_id, target_id, relation_type) VALUES (?, ?, ?)"
)

_SELECT_NODES_SQL = "SELECT concept_id FROM nodes ORDER BY concept_id"

_SELECT_EDGES_SQL = (
    "SELECT source_id, target_id, relation_type FROM edges "
    "ORDER BY source_id, target_id, relation_type"
)
"""`relation_type` is included last in the `ORDER BY` so a `NULL` (untyped)
row and one or more typed rows for the same `(source_id, target_id)` pair
sort together, `NULL` first -- SQLite's default ascending-order behavior for
`NULL` requires no explicit `CASE`/`COALESCE`."""

_SELECT_NEIGHBORS_SQL = (
    "SELECT target_id FROM edges WHERE source_id = ? ORDER BY target_id"
)


def _skip_note(concept_id: str, *, reason: str) -> str:
    """Build one skip notice, `fts.py`/`lint.collect_docs`-shaped."""
    return f"{concept_id}.md: skipped ({reason})"


class SqliteGraphStore:
    """A rebuild-per-run node-edge projection; owns its `sqlite3` connection.

    A context manager (mirrors `FtsIndex`): `with build_graph(bundle) as
    store: ...` closes the in-memory connection on block exit, dropping the
    database. Satisfies `graph.base.GraphStore` structurally via its
    `nodes()`/`edges()`/`neighbors()` query methods, each reading the
    `nodes`/`edges` tables with an explicit `ORDER BY` so results are sorted
    and deterministic regardless of insertion order.
    """

    skipped: list[str]
    """One note per unreadable/unparseable doc skipped during the build,
    shaped like `fts.py`'s skip notices."""

    def __init__(self, conn: sqlite3.Connection, skipped: list[str]) -> None:
        """Wrap an already-populated `conn` and its build-time `skipped` notes."""
        self._conn = conn
        self.skipped = skipped

    def nodes(self) -> list[str]:
        """Return every node id (OKF concept id) in the projection, sorted."""
        rows = self._conn.execute(_SELECT_NODES_SQL).fetchall()
        return [str(row[0]) for row in rows]

    def edges(self) -> list[Edge]:
        """Return every edge in the projection as `Edge` instances, sorted
        by `(source_id, target_id, relation_type)` (`NULL` -- untyped --
        first for a given pair)."""
        rows = self._conn.execute(_SELECT_EDGES_SQL).fetchall()
        return [
            Edge(source_id=str(row[0]), target_id=str(row[1]), relation_type=row[2])
            for row in rows
        ]

    def neighbors(self, concept_id: str) -> list[str]:
        """Return the out-edge target node ids for `concept_id`, sorted.

        Degrades to `[]` for a `concept_id` with no out-edges, and for a
        `concept_id` that is not even a node in the projection -- never
        raises."""
        rows = self._conn.execute(_SELECT_NEIGHBORS_SQL, (concept_id,)).fetchall()
        return [str(row[0]) for row in rows]

    def close(self) -> None:
        """Close the underlying connection, dropping the in-memory database."""
        self._conn.close()

    def __enter__(self) -> "SqliteGraphStore":
        """Return `self` -- the connection is already open by construction."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the connection on block exit, regardless of exception state."""
        self.close()


def build_graph(bundle_dir: Path) -> SqliteGraphStore:
    """Build an in-memory node-edge projection over every eligible doc under
    `bundle_dir`.

    Opens `sqlite3(":memory:")`, creates the `nodes`/`edges` tables (plain
    `CREATE TABLE`, no migration needed for a later `:memory:`-to-file flip),
    then walks `okf._iter_docs` once: a `read_error`/`parse_error` doc is
    skipped and noted, never crashing the build (mirrors `fts.build_index`);
    a valid doc has its body AND metadata re-read and re-parsed via
    `okf.load_frontmatter` (the same TOCTOU guard `fts.build_index` uses) and
    becomes one node. Edges are then extracted in two independent passes over
    that same doc set -- untyped from body links, typed from `relations:`
    frontmatter -- as documented at module level. Any exception raised
    anywhere in this build closes the in-memory connection before
    propagating.
    """
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(_CREATE_NODES_SQL)
        conn.execute(_CREATE_EDGES_SQL)
        conn.execute(_CREATE_EDGES_SOURCE_INDEX_SQL)
        conn.execute(_CREATE_EDGES_TARGET_INDEX_SQL)

        skipped: list[str] = []
        node_ids: set[str] = set()
        bodies: list[tuple[str, str]] = []
        metadatas: list[tuple[str, dict[str, object]]] = []
        for scan in okf._iter_docs(bundle_dir):
            concept_id = scan.path.relative_to(bundle_dir).with_suffix("").as_posix()
            if scan.read_error is not None:
                skipped.append(_skip_note(concept_id, reason="unreadable"))
                continue
            if scan.parse_error is not None:
                skipped.append(_skip_note(concept_id, reason="unparseable frontmatter"))
                continue
            try:
                text = scan.path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                skipped.append(_skip_note(concept_id, reason="unreadable"))
                continue
            try:
                metadata, body = okf.load_frontmatter(text)
            except Exception:  # broad: a concurrent edit can corrupt frontmatter
                skipped.append(_skip_note(concept_id, reason="unparseable frontmatter"))
                continue

            conn.execute(_INSERT_NODE_SQL, (concept_id,))
            node_ids.add(concept_id)
            bodies.append((concept_id, body))
            metadatas.append((concept_id, metadata))

        edge_pairs: set[tuple[str, str]] = set()
        for source_id, body in bodies:
            for match in _LINK_RE.finditer(_mask_fenced_code_blocks(body)):
                target_id = match.group(1).removesuffix(".md")
                if target_id in node_ids:
                    edge_pairs.add((source_id, target_id))
        for source_id, target_id in sorted(edge_pairs):
            conn.execute(_INSERT_EDGE_SQL, (source_id, target_id, None))

        typed_edges: set[tuple[str, str, str]] = set()
        for source_id, metadata in metadatas:
            try:
                relations = okf.decode_relations(metadata)
            except ValueError:  # malformed relations: contributes no typed edges
                skipped.append(_skip_note(source_id, reason="malformed relations"))
                continue
            for relation in relations:
                if relation.target in node_ids:
                    typed_edges.add((source_id, relation.target, relation.type))
        for source_id, target_id, relation_type in sorted(typed_edges):
            conn.execute(_INSERT_EDGE_SQL, (source_id, target_id, relation_type))
    except BaseException:
        conn.close()
        raise

    return SqliteGraphStore(conn, skipped)
