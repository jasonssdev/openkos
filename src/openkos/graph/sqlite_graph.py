"""In-memory SQLite node-edge projection over the compiled bundle, plus
(Slice 5) an on-disk persisted variant written only by `reindex`.

The derived-layer counterpart to `state/fts.py`: `build_graph` mirrors
`build_index` EXACTLY -- a rebuild-per-run `sqlite3(":memory:")` connection,
a single `okf._iter_docs` pass, and a TOCTOU-guarded body re-read, with
unreadable/unparseable docs skipped and noted rather than crashing the build.
Nodes are OKF concept ids (bundle-relative path, `.md` suffix removed), one
per non-reserved doc `_iter_docs` yields -- the same identity `fts.py` and
`forget` use. Edges come from TWO independent passes over the same doc set,
inserted as separate rows even between the same `(source_id, target_id)`
pair:

1. UNTYPED-OR-PROVENANCE-MIRROR, from `_LINK_RE`: a bundle-relative
   `[text](/….md)` markdown link in the doc body, with any `#anchor`
   stripped. `relation_type` is synthesized as `"derived_from"` (#135,
   provenance-mirror synthesis) IF AND ONLY IF the link's target id is a
   MEMBER of the source document's decoded `provenance:` frontmatter list
   (exact id match; membership only, never derived from link text or
   heading) -- otherwise `relation_type` stays `NULL`, unchanged from
   before. This is projection-READ-TIME synthesis only: it never writes to
   `relations:` frontmatter, never mutates bundle bytes, and never changes
   ingest byte-identity; a non-list `provenance:` value degrades to an
   empty membership set, and a non-string list entry is dropped, rather
   than crashing the build. Edges to a target that does not resolve to a
   known node in the same projection (external, non-bundle-relative,
   non-`.md`, or dangling) are dropped silently -- the build never raises
   because of them. A doc body is fence-masked (`_mask_fenced_code_blocks`)
   before edge extraction, so a link inside a fenced code block (e.g. raw
   ingested source material embedded verbatim under `## Source content`,
   see `okf.build_source_concept`) never produces a spurious edge, while
   the same link in ordinary prose or `## Related` still resolves.
2. TYPED, from the doc's `relations:` frontmatter (`okf.decode_relations`):
   one edge per entry whose `target` resolves to a known node, carrying that
   entry's `type` as `relation_type`. A `relations:` entry whose `target`
   does not resolve is dropped silently -- the same drop-if-unresolvable
   rule the untyped pass already applies. A doc whose `relations:` fails to
   decode (malformed shape) contributes no typed edges rather than crashing
   the build, mirroring this module's existing degrade-not-crash posture.

3. CANDIDATE, from an INJECTED `candidates` source (#183, pass 3). Optional:
   with `candidates=None` -- the default, and what a bundle with no
   `vectors.db` gets -- this pass is a complete no-op and the projection is
   byte-identical to the two-pass build. When a source IS given, each
   nominated pair becomes ONE row with `relation_type = NULL`. Proximity
   nominates a pair for a human to consider; it never claims what the
   relationship IS, which is why these rows are untyped and why
   `suggest-relations` still asks an LLM and `relate` still asks a human.
   A pair is dropped if either endpoint is not a known node (a stale
   `vectors.db` can name a forgotten concept), if the two endpoints are the
   same, or if EITHER DIRECTION already carries an edge from pass 1 or
   pass 2 -- a pair the bundle already links, or that a human already typed,
   needs no candidate. Rows are collapsed to one canonical `(min, max)`
   direction, because k-NN is near-symmetric and two rows would double every
   suggestion a human is asked to review. A document whose OKF `type` is
   `Source` is excluded from the seeding node set on BOTH ends (#378 slice
   1): a Source MUST NOT propose a candidate edge and MUST NOT receive one.
   This exclusion applies ONLY to pass 3 -- passes 1 and 2, including the
   Concept->Source `derived_from` provenance mirror, are unaffected. The
   surviving, deduped candidates are then RANKED by `distance` ascending
   (closest first), tie-broken by `(source_id, target_id)`, and truncated to
   `_MAX_CANDIDATE_EDGES` (#378 slice 2) -- a fixed per-run ceiling on
   candidate output. Truncation is never silent: `SqliteGraphStore
   .candidate_report` (a `CandidateReport(produced, retained)`) reports the
   pre-cap and post-cap counts on every build, `produced == retained` when
   the ceiling was not reached. The retained slice is inserted in
   ID-sorted, not distance-sorted, order, so an under-cap bundle's output
   stays byte-identical to the pre-#378-slice-2 build.

Each pass dedupes its own rows before insert -- the untyped pass on
`(source_id, target_id)`, the typed pass on `(source_id, target_id,
relation_type)`, the candidate pass on the canonical unordered pair -- and
all are inserted in sorted order so a rebuild over an unchanged bundle is
deterministic. A typed edge and an untyped edge between the same pair are
DISTINCT rows: this dedup key is why a doc can have both a `## Related` link
AND a `relations:` entry pointing at the same target without collapsing into
one row. Pass 3 is the exception -- it defers to both, never adding a row
for a pair either has already claimed.

Any exception during the build closes the in-memory connection before
propagating, so a failed build never leaks it -- only a successful build
hands the open connection off to the returned `SqliteGraphStore`.

`_populate_graph_tables` is the shared node/edge-population core both
`build_graph` (against a fresh `:memory:` connection) and `write_graph_store`
(against an on-disk connection opened via `state/derived.py`) delegate to --
one doc-walk/extraction implementation, two targets, mirroring `state/fts.py`'s
`_populate_docs_table` split exactly.

`write_graph_store(path, bundle_dir)` (Slice 5, `derived-index-cache`) is
invoked ONLY by `reindex`: it always performs a full rebuild (DROP + repopulate)
against `.openkos/graph.db`, targeted via `derived.open_derived_connection`'s
WAL/busy_timeout/lazy-create posture. The DROP, rebuild, and manifest write
all happen inside one explicit SQLite transaction (mirrors `state/fts.py`'s
atomicity fix): a crash mid-rebuild rolls back completely rather than
leaving an empty projection paired with a stale-but-unchanged manifest hash.
`manifest_hash`, when given, is stored VERBATIM rather than recomputed here
-- `state/reindex.py`'s decision digest is the single source of truth, never
a second/third independently-taken bundle walk. The SKIP-vs-REBUILD decision
itself lives entirely in `state/reindex.py`; this function never decides
whether to run, only how to write when called. `open_graph_store_readonly`
is the read-only counterpart every non-`reindex` consumer uses:
existence-gated (returns `None` for an absent file, never creating one),
opened via a `file:...?mode=ro` URI connection so a write attempt against
the returned handle fails at the SQLite level, and it NEVER computes or
compares a manifest hash -- staleness detection is exclusively `reindex`'s
job (D2 binding contract).

Layering boundary: the canonical layer (`openkos.model`, `openkos.bundle`,
`openkos.state`) MUST NOT import `openkos.graph`; `openkos.graph` (derived)
importing `openkos.state.derived` (canonical) below is the ALLOWED direction
-- derived depends on canonical, never the reverse.
"""

import json
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Final, Literal, Protocol

from openkos.graph.base import Edge
from openkos.model import okf
from openkos.state import derived
from openkos.state.readonly import open_read_only


class ProximityPairLike(Protocol):
    """The shape pass 3 reads off a candidate pair.

    Structural rather than an import of `graph.proximity.ProximityPair`:
    this module must stay ignorant of where candidates come from, so a test
    can hand it a stub with no `vectors.db` in sight and a future source
    (a different embedding backend, a co-citation heuristic) needs no change
    here."""

    @property
    def source_id(self) -> str: ...

    @property
    def target_id(self) -> str: ...

    @property
    def distance(self) -> float: ...


class CandidateSource(Protocol):
    """Injected supplier of candidate concept pairs for pass 3."""

    def pairs(self, concept_ids: Sequence[str]) -> Sequence[ProximityPairLike]:
        """Nominate pairs among `concept_ids`. Must never raise."""
        ...


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

SCHEMA_VERSION: Final = "1"
"""Layout version of `graph.db`. A store written under a different version
is rebuilt whole rather than updated per document."""

SCHEMA_VERSION_KEY: Final = "schema_version"

_CREATE_DOC_OUTLINKS_SQL = """
CREATE TABLE doc_outlinks (
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    relation_type TEXT
)
"""
"""Every outgoing reference a document makes, resolved or not (`kind` is
`link` for a body link -- `relation_type` is the synthesized `derived_from`
or `NULL` -- and `relation` for a `relations:` entry). `edges` rows for passes
1 and 2 are exactly the outlinks whose target is a node; keeping the
unresolved ones is what lets a new document recover the links that named it
before it existed."""

_CREATE_DOC_OUTLINKS_SOURCE_INDEX_SQL = (
    "CREATE INDEX idx_doc_outlinks_source_id ON doc_outlinks (source_id)"
)

_CREATE_DOC_OUTLINKS_TARGET_INDEX_SQL = (
    "CREATE INDEX idx_doc_outlinks_target_id ON doc_outlinks (target_id)"
)

_CREATE_NODE_FACTS_SQL = """
CREATE TABLE node_facts (
    concept_id TEXT PRIMARY KEY,
    is_source INTEGER NOT NULL,
    quarantined INTEGER NOT NULL,
    provenance TEXT NOT NULL
)
"""
"""What pass 3 needs to know about a node without re-reading its document:
whether it is a `Source`, whether that Source carries a quarantine notice, and
its decoded `provenance:` list (JSON array of strings)."""

_CREATE_DOC_MANIFEST_SQL = """
CREATE TABLE doc_manifest (
    concept_id TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL
)
"""

_STORE_TABLES: Final = (
    "nodes",
    "edges",
    "doc_outlinks",
    "node_facts",
    "doc_manifest",
)

_INSERT_NODE_SQL = "INSERT INTO nodes (concept_id) VALUES (?)"

_INSERT_OUTLINK_SQL = (
    "INSERT INTO doc_outlinks (source_id, target_id, kind, relation_type) "
    "VALUES (?, ?, ?, ?)"
)

_INSERT_FACTS_SQL = (
    "INSERT INTO node_facts (concept_id, is_source, quarantined, provenance) "
    "VALUES (?, ?, ?, ?)"
)

_MATERIALISE_ALL_EDGES_SQL = (
    "INSERT INTO edges (source_id, target_id, relation_type) "
    "SELECT source_id, target_id, relation_type FROM doc_outlinks "
    "WHERE target_id IN (SELECT concept_id FROM nodes) "
    "ORDER BY kind, source_id, target_id, relation_type"
)

_MATERIALISE_TOUCHED_EDGES_SQL = (
    "INSERT INTO edges (source_id, target_id, relation_type) "
    "SELECT source_id, target_id, relation_type FROM doc_outlinks "
    "WHERE target_id IN (SELECT concept_id FROM nodes) "
    "AND (source_id IN (SELECT concept_id FROM touched) "
    "OR target_id IN (SELECT concept_id FROM touched)) "
    "ORDER BY kind, source_id, target_id, relation_type"
)

_DELETE_CANDIDATE_EDGES_SQL = (
    "DELETE FROM edges WHERE relation_type IS NULL AND NOT EXISTS ("
    "SELECT 1 FROM doc_outlinks o WHERE o.kind = 'link' "
    "AND o.relation_type IS NULL "
    "AND o.source_id = edges.source_id AND o.target_id = edges.target_id)"
)
"""Pass-3 candidate edges are the untyped edges no body link accounts for."""

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


@dataclass(frozen=True)
class WithheldCandidate:
    """One candidate pair pass 3 withheld under #841's unjudged-source
    gate, with the quarantined source(s) both endpoints cite. The
    attribution is stored per pair -- not as one flat source list beside a
    flat pair list -- because the disclosure must name ONLY sources at
    least one VISIBLE pair is attributed to: filtering pairs and sources
    independently lets a caller without confidential access learn that a
    source's (entirely confidential) pairs exist."""

    pair: tuple[str, str]
    """Canonical `(min, max)` endpoint ids."""
    source_ids: tuple[str, ...]
    """The quarantined Source concept ids both endpoints cite, sorted."""


_MAX_CANDIDATE_EDGES: Final[int] = 50
"""Hard ceiling on candidate edges one build may emit. Bounds `curate`'s
one-LLM-call-per-untyped-edge run to ~2-4 minutes at 3-5s/call instead of the
17m19s a 74-candidate run cost (#378). Sits above the reported bundle's ~25
post-Source-filter volume (so today's output is unchanged) and below
`contradiction._MAX_PAIRS = 200`, which remains the downstream backstop on
work EXECUTED. Truncation is NEVER silent -- see `CandidateReport`."""


@dataclass(frozen=True)
class CandidateReport:
    """The pass-3 candidate-edge truncation report (#378 slice 2, design
    D4; `pairs` added by the #378 post-review correction). `produced` is the
    ranked, Source-excluded, DEDUPED count BEFORE the `_MAX_CANDIDATE_EDGES`
    cap is applied; `retained` is the count actually inserted. `produced >
    retained` is the RAW (unfiltered) cap-reached signal; all three default
    to `0`/`()` for a build with `candidates=None`, where pass 3 never runs.

    `pairs` is the full pre-cap candidate set as canonical `(source_id,
    target_id)` tuples, in the SAME ranked order (distance ascending,
    `(source_id, target_id)` tie-break) the cap slices -- `len(pairs) ==
    produced` and `pairs[:retained]` is exactly the slice pass 3 actually
    inserted. Pass 3 has NO sensitivity awareness of its own (it runs before
    any confidentiality filter), so `produced`/`retained` here can count
    pairs a given caller is not allowed to see. A caller that must respect
    the sensitivity-fail-closed-filter (e.g. a CLI truncation notice) MUST
    NOT render `produced`/`retained` directly -- it must re-derive both
    counts by filtering `pairs` through its own `sensitivity
    .sensitive_concept_ids` walk first (see
    `resolution.edge_typing.candidate_truncation_notice`)."""

    produced: int = 0
    retained: int = 0
    pairs: tuple[tuple[str, str], ...] = ()
    offset: int = 0
    """How many ranked pairs the build SKIPPED before its retained window
    (#567 paging): the inserted slice is `pairs[offset : offset + retained]`,
    so the pre-#567 `pairs[:retained]` invariant is the `offset == 0`
    special case. Defaults to `0` so every existing constructor and the
    `candidates=None` build are untouched."""
    quarantine_withheld: tuple["WithheldCandidate", ...] = ()
    """The pairs pass 3 WITHHELD because both endpoints derive from one
    source under #772's judge-degrade quarantine (#841), sorted by pair,
    each carrying the quarantined source(s) it was attributed to. Withheld
    BEFORE `best`, so these never enter `pairs`, never consume a cap slot,
    and never shift the paging window. Same sensitivity caveat as `pairs`:
    raw, unfiltered -- a notice must re-derive its visible count AND name
    only sources a visible pair is actually attributed to
    (`resolution.edge_typing.quarantined_candidate_notice`); the
    attribution rides each entry precisely so that restriction is
    computable. Defaulted empty so every existing constructor is
    untouched."""


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

    candidate_report: CandidateReport
    """The pass-3 truncation report (#378 slice 2). Defaults to
    `CandidateReport()` (0/0) so `open_graph_store_readonly` -- which never
    runs pass 3 -- stays untouched."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        skipped: list[str],
        candidate_report: CandidateReport | None = None,
    ) -> None:
        """Wrap an already-populated `conn` and its build-time `skipped` notes
        and `candidate_report` (defaulted so every pre-#378-slice-2 caller,
        including `open_graph_store_readonly`, needs no change)."""
        self._conn = conn
        self.skipped = skipped
        self.candidate_report = (
            candidate_report if candidate_report is not None else CandidateReport()
        )

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


def _read_doc(path: Path) -> tuple[dict[str, object], str] | str:
    """Read and parse `path` into `(metadata, body)`, or return the skip
    reason. The one place a document becomes graph input, shared by the whole
    rebuild and the per-document update so the two can never disagree on what
    a document contributes. The read is guarded (`fts.py`-shaped): a doc that
    vanishes or corrupts after the walk is reported, not raised."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "unreadable"
    try:
        return okf.load_frontmatter(text)
    except okf.FrontmatterError:  # a concurrent edit can corrupt frontmatter
        return "unparseable frontmatter"


def _store_doc(
    conn: sqlite3.Connection,
    concept_id: str,
    metadata: dict[str, object],
    body: str,
) -> str | None:
    """Record one document: its node, its pass-3 facts, and every outgoing
    reference it makes (`doc_outlinks`), resolved or not. Edges are NOT
    written here -- they are materialised from the outlinks once the node set
    is known. Returns a skip note when the document's `relations:` block is
    malformed (it then contributes no typed outlinks), else `None`."""
    raw_provenance = metadata.get("provenance")
    provenance = sorted(
        {entry for entry in raw_provenance if isinstance(entry, str)}
        if isinstance(raw_provenance, list)
        else set()
    )
    is_source = metadata.get("type") == "Source"
    # #841: a Source is quarantined when its extraction notices carry either
    # judge-degrade marker.
    quarantined = is_source and bool(
        {
            okf.EXTRACTION_NOTICE_JUDGE_UNAVAILABLE,
            okf.EXTRACTION_NOTICE_JUDGE_EMPTY,
        }
        & set(okf.extraction_notices(metadata))
    )
    conn.execute(_INSERT_NODE_SQL, (concept_id,))
    conn.execute(
        _INSERT_FACTS_SQL,
        (concept_id, int(is_source), int(quarantined), json.dumps(provenance)),
    )
    link_targets = {
        match.group(1).removesuffix(".md")
        for match in _LINK_RE.finditer(_mask_fenced_code_blocks(body))
    }
    for target_id in sorted(link_targets):
        relation_type = "derived_from" if target_id in provenance else None
        conn.execute(
            _INSERT_OUTLINK_SQL, (concept_id, target_id, "link", relation_type)
        )
    try:
        relations = okf.decode_relations(metadata)
    except ValueError:  # malformed relations: contributes no typed edges
        return _skip_note(concept_id, reason="malformed relations")
    for target_id, relation_type in sorted(
        {(relation.target, relation.type) for relation in relations}
    ):
        conn.execute(
            _INSERT_OUTLINK_SQL, (concept_id, target_id, "relation", relation_type)
        )
    return None


def _candidate_pass(
    conn: sqlite3.Connection,
    candidates: CandidateSource,
    candidate_offset: int,
) -> CandidateReport:
    """Pass 3: nominate, filter, rank and cap proximity candidates, inserting
    the retained ones as untyped edges. Reads only the store (`node_facts` and
    the edges already materialised), never the bundle -- which is what lets
    the per-document refresh recompute it globally and cheaply. The caller
    must have removed any prior candidate edges, so the `edges` table holds
    only the pass 1 and 2 rows."""
    # Dedup against BOTH prior passes, in both directions. A pair a human
    # already typed via `relations:` would otherwise gain a redundant NULL
    # row -- filtered right back out of suggestions by `edge_typing`'s
    # pair-level exclusion, but still inflating `graph_edge_summary`'s total.
    # Both directions because links are directed and proximity is not.
    seen: set[tuple[str, str]] = set()
    for source_id, target_id in conn.execute("SELECT source_id, target_id FROM edges"):
        seen.add((source_id, target_id))
        seen.add((target_id, source_id))
    provenance_by_source: dict[str, set[str]] = {}
    seed_node_ids: set[str] = set()
    quarantined_sources: set[str] = set()
    for concept_id, is_source, quarantined, provenance in conn.execute(
        "SELECT concept_id, is_source, quarantined, provenance FROM node_facts"
    ):
        provenance_by_source[concept_id] = set(json.loads(provenance))
        if not is_source:
            seed_node_ids.add(concept_id)
        if quarantined:
            quarantined_sources.add(concept_id)
    # Source-exclusion (#378 slice 1): a `Source` document must not propose
    # (anchor list) or receive (row guards) a candidate edge. Both endpoints
    # are checked against the Source-free `seed_node_ids`, because
    # `VectorProximitySource.pairs` queries the whole `vectors.db` and can
    # return a Source as a neighbor even when it was never offered as an anchor.
    # #378 slice 2: a `dict` keyed by the canonical `(min, max)` pair keeps the
    # SMALLEST distance per pair. Endpoint guard, self-pair drop, and `seen`
    # dedup all run before ranking, so `best` already holds the fully deduped
    # set BEFORE the cap (filter before cap: discarded rows must not consume
    # cap slots and starve eligible ones).
    # #841: a pair is withheld exactly when both endpoints CITE a common
    # quarantined source (`provenance:`) -- near-boilerplate pairs from one
    # degraded extraction. An endpoint with no `provenance:` cannot cite a
    # quarantined source and keeps its pair (attribution fails open).
    quarantine_dropped: dict[tuple[str, str], set[str]] = {}
    best: dict[tuple[str, str], float] = {}
    for pair in candidates.pairs(sorted(seed_node_ids)):
        if pair.source_id not in seed_node_ids or pair.target_id not in seed_node_ids:
            continue
        if pair.source_id == pair.target_id:
            continue
        if (pair.source_id, pair.target_id) in seen:
            continue
        key = (
            min(pair.source_id, pair.target_id),
            max(pair.source_id, pair.target_id),
        )
        common_quarantined = (
            provenance_by_source.get(pair.source_id, set())
            & provenance_by_source.get(pair.target_id, set())
            & quarantined_sources
        )
        if common_quarantined:
            quarantine_dropped.setdefault(key, set()).update(common_quarantined)
            continue
        if key not in best or pair.distance < best[key]:
            best[key] = pair.distance
    # Rank by distance ascending, tie-broken by `(source_id, target_id)`, THEN
    # slice to the cap. #567 paging: the window slides by `candidate_offset`
    # ranked pairs; an offset at or past the set retains nothing.
    ranked = sorted(best, key=lambda pair_key: (best[pair_key], pair_key))
    retained_keys = ranked[candidate_offset : candidate_offset + _MAX_CANDIDATE_EDGES]
    for source_id, target_id in sorted(retained_keys):
        conn.execute(_INSERT_EDGE_SQL, (source_id, target_id, None))
    return CandidateReport(
        produced=len(best),
        retained=len(retained_keys),
        pairs=tuple(ranked),
        offset=candidate_offset,
        quarantine_withheld=tuple(
            WithheldCandidate(pair=pair, source_ids=tuple(sorted(sources)))
            for pair, sources in sorted(quarantine_dropped.items())
        ),
    )


def _populate_graph_tables(
    conn: sqlite3.Connection,
    bundle_dir: Path,
    *,
    candidates: CandidateSource | None = None,
    candidate_offset: int = 0,
) -> tuple[list[str], CandidateReport]:
    """Shared node/edge-population core (D-refactor, dedupes the in-memory/
    on-disk writer paths): creates the graph tables + indexes on `conn`, then
    walks `okf._iter_docs(bundle_dir)` once and records each document's node,
    pass-3 facts and outgoing references (`_store_doc`), returning the skip
    notices for anything that could not be projected, plus the pass-3
    truncation report (#378 slice 2).

    A `read_error`/`parse_error` doc is skipped and noted, never crashing
    the build (mirrors `fts.build_index`); a valid doc has its body AND
    metadata re-read and re-parsed (the same TOCTOU guard `fts.build_index`
    uses) and becomes one node. Edges are then materialised from the
    recorded outlinks whose target is a node, exactly as documented at module
    level: body links are `NULL` UNLESS the link's target is a member of the
    source doc's `provenance:` list, in which case `derived_from` (#135,
    projection-read-time only); `relations:` entries always carry their
    explicit `type`. A third pass runs ONLY when `candidates` is given
    (#183): see `_candidate_pass`. With `candidates=None` the third pass does
    not run and the returned `CandidateReport` is the zero-valued default.
    Callers own `conn`'s lifecycle -- any exception raised here propagates to
    the caller unchanged, closing/cleanup is the caller's responsibility.
    """
    conn.execute(_CREATE_NODES_SQL)
    conn.execute(_CREATE_EDGES_SQL)
    conn.execute(_CREATE_EDGES_SOURCE_INDEX_SQL)
    conn.execute(_CREATE_EDGES_TARGET_INDEX_SQL)
    conn.execute(_CREATE_DOC_OUTLINKS_SQL)
    conn.execute(_CREATE_DOC_OUTLINKS_SOURCE_INDEX_SQL)
    conn.execute(_CREATE_DOC_OUTLINKS_TARGET_INDEX_SQL)
    conn.execute(_CREATE_NODE_FACTS_SQL)

    skipped: list[str] = []
    malformed: list[str] = []
    for scan in okf._iter_docs(bundle_dir):
        concept_id = okf.concept_id_for(scan.path, bundle_dir)
        if scan.read_error is not None:
            skipped.append(_skip_note(concept_id, reason="unreadable"))
            continue
        if scan.parse_error is not None:
            skipped.append(_skip_note(concept_id, reason="unparseable frontmatter"))
            continue
        doc = _read_doc(scan.path)
        if isinstance(doc, str):
            skipped.append(_skip_note(concept_id, reason=doc))
            continue
        note = _store_doc(conn, concept_id, doc[0], doc[1])
        if note is not None:
            malformed.append(note)
    skipped.extend(malformed)

    conn.execute(_MATERIALISE_ALL_EDGES_SQL)
    candidate_report = CandidateReport()
    if candidates is not None:
        candidate_report = _candidate_pass(conn, candidates, candidate_offset)
    return skipped, candidate_report


def build_graph(
    bundle_dir: Path,
    *,
    candidates: CandidateSource | None = None,
    candidate_offset: int = 0,
) -> SqliteGraphStore:
    """Build an in-memory node-edge projection over every eligible doc under
    `bundle_dir`.

    Opens `sqlite3(":memory:")` and delegates to `_populate_graph_tables` for
    the table DDL + doc-walk + node/edge-extraction sequence. Any exception
    raised anywhere in that call closes the in-memory connection before
    propagating -- a failed build never leaks it; only a successful build
    hands the open connection off to the returned `SqliteGraphStore`. Never
    touches disk -- persistence exists only via the distinct
    `write_graph_store` path `reindex` calls (graph-projection: Projection
    never touches disk).
    """
    conn = sqlite3.connect(":memory:")
    try:
        skipped, candidate_report = _populate_graph_tables(
            conn, bundle_dir, candidates=candidates, candidate_offset=candidate_offset
        )
    except BaseException:
        conn.close()
        raise

    return SqliteGraphStore(conn, skipped, candidate_report)


def write_graph_store(
    path: Path,
    bundle_dir: Path,
    *,
    manifest_hash: str | None = None,
    candidates: CandidateSource | None = None,
) -> None:
    """Write a full, on-disk node-edge projection for `bundle_dir` to `path`,
    invoked ONLY by `reindex` (derived-index-cache: On-Disk Persistence Of
    Derived Indexes; graph-projection: Reindex persists the graph index to
    disk).

    Opens `path` via `state.derived.open_derived_connection` (lazy
    `.openkos/` creation, WAL + busy_timeout PRAGMAs, shared `meta` table),
    then drops any prior `nodes`/`edges` tables and delegates to the SAME
    `_populate_graph_tables` core `build_graph` uses -- the on-disk
    projection always contains exactly the nodes/edges an equivalent
    `build_graph` call would produce in memory. This function always
    performs a full rebuild when called -- it makes no skip/rebuild decision
    of its own; that comparison against the PREVIOUSLY stored manifest hash
    is `state/reindex.py`'s exclusive responsibility (D2 binding contract).

    `manifest_hash`, when given, is stored VERBATIM rather than recomputed
    here: `state/reindex.py`'s `_reindex_graph` passes the SAME digest it
    already computed for its skip/rebuild decision, so the stored value
    always corresponds to that exact bundle snapshot instead of a THIRD,
    independently-taken walk (mirrors `state/fts.py::write_fts_index`'s
    Finding-C correction). Omitting it (the default) computes one fresh, for
    direct/standalone callers with no separate decision step of their own.

    The `DROP`s, the full rebuild, and the manifest write ALL happen inside
    one explicit SQLite transaction (`BEGIN IMMEDIATE` ... `commit()`/
    `rollback()`), mirroring `state/fts.py::write_fts_index`'s Finding-B
    atomicity correction: a crash mid-rebuild rolls back completely, leaving
    the PRIOR `nodes`/`edges` tables and PRIOR `meta.manifest_hash` exactly
    as they were, rather than a structurally-valid but EMPTY/partial
    projection paired with a stale-but-unchanged manifest hash.
    """
    conn = derived.open_derived_connection(path)
    try:
        entries = derived.bundle_manifest_entries(bundle_dir)
        digest = (
            manifest_hash
            if manifest_hash is not None
            else derived.manifest_digest(
                (e.concept_id, e.content_hash) for e in entries
            )
        )
        _rebuild(conn, bundle_dir, entries, digest, candidates)
    finally:
        conn.close()


def _rebuild(
    conn: sqlite3.Connection,
    bundle_dir: Path,
    entries: list[derived.ManifestEntry],
    digest: str,
    candidates: CandidateSource | None,
) -> None:
    """Whole rebuild of every graph table + `doc_manifest` + meta in ONE
    explicit transaction (see `write_graph_store` for why it is explicit).
    `entries` are recorded as the store's per-document baseline."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        for table in _STORE_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        _populate_graph_tables(conn, bundle_dir, candidates=candidates)
        conn.execute(_CREATE_DOC_MANIFEST_SQL)
        conn.executemany(
            "INSERT OR REPLACE INTO doc_manifest (concept_id, content_hash) "
            "VALUES (?, ?)",
            [(e.concept_id, e.content_hash) for e in entries],
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (SCHEMA_VERSION_KEY, SCHEMA_VERSION),
        )
        derived.write_manifest_hash(conn, digest)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _recorded_pairs(conn: sqlite3.Connection) -> dict[str, str] | None:
    """The store's recorded `{concept_id: content_hash}` baseline, or `None`
    when it cannot be trusted to drive a per-document update: a table missing
    (a store from before per-document maintenance), a `schema_version` that
    differs from this code's, recorded pairs whose digest does not reproduce
    the stored `manifest_hash`, or nodes/outlinks/facts for documents the
    manifest does not record."""
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if not set(_STORE_TABLES) <= tables:
        return None
    version = conn.execute(
        "SELECT value FROM meta WHERE key = ?", (SCHEMA_VERSION_KEY,)
    ).fetchone()
    if version is None or str(version[0]) != SCHEMA_VERSION:
        return None
    recorded = {
        str(cid): str(chash)
        for cid, chash in conn.execute(
            "SELECT concept_id, content_hash FROM doc_manifest"
        )
    }
    if derived.manifest_digest(recorded.items()) != derived.read_manifest_hash(conn):
        return None
    stray = conn.execute(
        "SELECT 1 FROM ("
        "SELECT concept_id FROM nodes UNION SELECT concept_id FROM node_facts "
        "UNION SELECT source_id FROM doc_outlinks) "
        "WHERE concept_id NOT IN (SELECT concept_id FROM doc_manifest) LIMIT 1"
    ).fetchone()
    if stray is not None:  # state no recorded document accounts for
        return None
    return recorded


def _apply_incremental(
    conn: sqlite3.Connection,
    recorded: dict[str, str],
    entries: list[derived.ManifestEntry],
    digest: str,
    candidates: CandidateSource | None,
) -> None:
    """Rewrite only the added, changed and removed documents' nodes, facts,
    outlinks and edges, re-resolve every outlink that names a touched
    document, recompute the global candidate pass, and update the manifest --
    in one transaction. The outlink table keeps references to targets that did
    not exist, so a link naming a document that has just appeared is
    recovered here without re-reading its source."""
    current = {e.concept_id: e for e in entries}
    removed = [cid for cid in recorded if cid not in current]
    touched = [
        cid for cid, entry in current.items() if recorded.get(cid) != entry.content_hash
    ]
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Candidate edges are the untyped edges no body link accounts for;
        # identify them while the outlinks still describe the old documents.
        conn.execute(_DELETE_CANDIDATE_EDGES_SQL)
        conn.execute("CREATE TEMP TABLE touched (concept_id TEXT PRIMARY KEY)")
        conn.executemany(
            "INSERT INTO touched (concept_id) VALUES (?)",
            [(cid,) for cid in (*removed, *touched)],
        )
        conn.execute(
            "DELETE FROM edges WHERE source_id IN (SELECT concept_id FROM touched) "
            "OR target_id IN (SELECT concept_id FROM touched)"
        )
        for table, column in (
            ("nodes", "concept_id"),
            ("node_facts", "concept_id"),
            ("doc_outlinks", "source_id"),
        ):
            conn.execute(
                f"DELETE FROM {table} WHERE {column} IN "  # noqa: S608 -- fixed names
                "(SELECT concept_id FROM touched)"
            )
        for cid in removed:
            conn.execute("DELETE FROM doc_manifest WHERE concept_id = ?", (cid,))
        for cid in touched:
            doc = _read_doc(current[cid].path)
            if not isinstance(doc, str):  # a skipped doc keeps no node, as in a rebuild
                _store_doc(conn, cid, doc[0], doc[1])
            conn.execute(
                "INSERT OR REPLACE INTO doc_manifest (concept_id, content_hash) "
                "VALUES (?, ?)",
                (cid, current[cid].content_hash),
            )
        conn.execute(_MATERIALISE_TOUCHED_EDGES_SQL)
        conn.execute("DROP TABLE touched")
        if candidates is not None:
            _candidate_pass(conn, candidates, 0)
        derived.write_manifest_hash(conn, digest)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _try_incremental(
    conn: sqlite3.Connection,
    recorded: dict[str, str],
    entries: list[derived.ManifestEntry],
    digest: str,
    candidates: CandidateSource | None,
) -> bool:
    """Run the per-document update; `False` means it failed (and rolled back),
    so the caller must rebuild whole. Lock contention is NOT a failure of the
    update itself -- a rebuild would meet the same lock -- so it propagates."""
    try:
        _apply_incremental(conn, recorded, entries, digest, candidates)
    except sqlite3.OperationalError as exc:
        if derived.is_lock_contention(exc):
            raise
        return False
    except Exception:  # noqa: BLE001 -- any failure falls back to a whole rebuild
        return False
    return True


def refresh_graph_store(
    path: Path,
    bundle_dir: Path,
    *,
    force: bool = False,
    candidates: CandidateSource | None = None,
) -> Literal["unchanged", "incremental", "rebuilt"]:
    """Bring the on-disk graph at `path` up to date with `bundle_dir`
    (derived-index-cache: Per-Document Update With Whole-Rebuild Fallback).

    The bundle's manifest hash against the stored one is still the only
    staleness gate: equal (and not `force`) -> `"unchanged"`, no write.
    Otherwise the recorded `doc_manifest` is diffed against the current
    documents and only the added/changed/removed ones are rewritten
    (`"incremental"`), with the candidate pass recomputed globally. Whole
    rebuild (`"rebuilt"`) is the fallback when `force` is set, the store has
    no trustworthy baseline (`_recorded_pairs`), or the per-document update
    fails for any reason other than lock contention (which propagates, since a
    rebuild would meet the same lock). The incremental transaction rolls back
    before the fallback runs, so a failed update never leaves a half-updated
    graph."""
    conn = derived.open_derived_connection(path)
    try:
        stored = derived.read_manifest_hash(conn)
        entries = derived.bundle_manifest_entries(bundle_dir)
        digest = derived.manifest_digest(
            (e.concept_id, e.content_hash) for e in entries
        )
        if not force and stored == digest:
            return "unchanged"
        if not force:
            recorded = _recorded_pairs(conn)
            if recorded is not None and _try_incremental(
                conn, recorded, entries, digest, candidates
            ):
                return "incremental"
        _rebuild(conn, bundle_dir, entries, digest, candidates)
        return "rebuilt"
    finally:
        conn.close()


def open_graph_store_readonly(path: Path) -> "SqliteGraphStore | None":
    """Open the on-disk graph projection at `path` read-only, for future
    `answer()` consumers (derived-index-cache: Consumers Read Persisted
    Indexes Read-Only; graph-projection: Persisted index read-only for
    non-reindex consumers).

    Existence-gated: returns `None` if `path` does not exist rather than
    creating one -- only `reindex`'s `write_graph_store` ever creates this
    file. Opens via a `file:...?mode=ro` SQLite URI connection, so the
    returned handle's connection genuinely refuses any write attempt at the
    SQLite level (not merely by convention). Immediately after connecting,
    runs one cheap validating read (`SELECT 1 FROM nodes LIMIT 1`) so a file
    that EXISTS but is not a valid SQLite database (or lacks the `nodes`
    table entirely) raises a `sqlite3.Error` HERE, at open time, giving the
    CLI's open-or-degrade layer a single, well-defined call site to catch
    (Slice 5, PR3, mirrors `state/fts.py::open_fts_index_readonly`'s
    identical validation-probe posture). NEVER computes or compares a
    bundle manifest hash -- staleness detection is exclusively `reindex`'s
    job; a caller here always gets whatever `reindex` last wrote, however
    stale.
    """
    if not path.exists():
        return None
    conn = open_read_only(path)
    try:
        conn.execute("SELECT 1 FROM nodes LIMIT 1")
    except BaseException:
        conn.close()
        raise
    return SqliteGraphStore(conn, [])


def reindex_graph(
    bundle_dir: Path,
    path: Path,
    *,
    force: bool = False,
    candidates: CandidateSource | None = None,
) -> None:
    """Rebuild the on-disk graph projection at `path` iff the bundle's
    manifest hash changed since the last run, or `force` (mirrors
    `state/reindex.py`'s `_reindex_fts` gate for the FTS store).

    A thin wrapper around `refresh_graph_store`: the manifest comparison is
    still the only staleness gate (D2 binding contract); a mismatch updates
    only the changed documents (candidate pass recomputed globally) and falls
    back to a whole rebuild when the store has no trustworthy per-document
    baseline.

    Deliberately lives HERE in `openkos.graph` rather than in
    `state/reindex.py`: `state/reindex.py` is canonical-layer code and MUST
    NOT import `openkos.graph` (derived layer) -- the entry layer
    (`cli/main.py`'s `reindex` command) calls `state.reindex.reindex(...)`
    for `vectors.db`/`fts.db` and THIS function separately for `graph.db`,
    within the same CLI invocation, so `openkos reindex` still writes all
    three derived stores in one run (reindex-command: Reindex writes all
    three derived stores in one run) without violating the documented
    canonical/derived layering boundary (docs/architecture.md).
    """

    refresh_graph_store(path, bundle_dir, force=force, candidates=candidates)
