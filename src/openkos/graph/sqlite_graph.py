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

import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Final, Protocol
from urllib.parse import quote

from openkos.graph.base import Edge
from openkos.model import okf
from openkos.state import derived


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


def _populate_graph_tables(
    conn: sqlite3.Connection,
    bundle_dir: Path,
    *,
    candidates: CandidateSource | None = None,
    candidate_offset: int = 0,
) -> tuple[list[str], CandidateReport]:
    """Shared node/edge-population core (D-refactor, dedupes the in-memory/
    on-disk writer paths): creates the `nodes`/`edges` tables + indexes on
    `conn`, then walks `okf._iter_docs(bundle_dir)` once and extracts nodes
    and edges exactly as documented at module level, returning the skip
    notices for anything that could not be projected, plus the pass-3
    truncation report (#378 slice 2).

    A `read_error`/`parse_error` doc is skipped and noted, never crashing
    the build (mirrors `fts.build_index`); a valid doc has its body AND
    metadata re-read and re-parsed via `okf.load_frontmatter` (the same
    TOCTOU guard `fts.build_index` uses) and becomes one node. Edges are
    then extracted in up to three independent passes over that same doc set,
    exactly as documented at module level: the first, from body links, is
    `NULL` UNLESS the link's target is a member of the source doc's
    `provenance:` frontmatter list, in which case it is synthesized as
    `derived_from` (#135, provenance-mirror synthesis, projection-read-time
    only); the second, from `relations:` frontmatter, always carries that
    entry's explicit `type`; the third runs ONLY when `candidates` is given
    (#183) and writes one untyped row per nominated pair that neither
    earlier pass already claimed, in either direction, EXCLUDING any pair
    where either endpoint's OKF `type` is `Source` (#378 slice 1) -- a
    Source document must not propose or receive a candidate edge, though it
    still participates fully in passes 1 and 2. The surviving candidates are
    then RANKED by `ProximityPair.distance` ascending, tie-broken by
    `(source_id, target_id)`, and truncated to `_MAX_CANDIDATE_EDGES` (#378
    slice 2) -- the retained slice is re-sorted by id before insert, so an
    under-cap bundle's output stays byte-identical to the pre-slice-2 build.
    With `candidates=None` -- the default -- the third pass does not run,
    the output is byte-identical to the two-pass build, and the returned
    `CandidateReport` is the zero-valued default. Callers own `conn`'s
    lifecycle -- any exception raised here propagates to the caller
    unchanged, closing/cleanup is the caller's responsibility.
    """
    conn.execute(_CREATE_NODES_SQL)
    conn.execute(_CREATE_EDGES_SQL)
    conn.execute(_CREATE_EDGES_SOURCE_INDEX_SQL)
    conn.execute(_CREATE_EDGES_TARGET_INDEX_SQL)

    skipped: list[str] = []
    node_ids: set[str] = set()
    bodies: list[tuple[str, str]] = []
    metadatas: list[tuple[str, dict[str, object]]] = []
    for scan in okf._iter_docs(bundle_dir):
        concept_id = okf.concept_id_for(scan.path, bundle_dir)
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

    provenance_by_source: dict[str, set[str]] = {}
    for source_id, metadata in metadatas:
        raw_provenance = metadata.get("provenance")
        if isinstance(raw_provenance, list):
            provenance_by_source[source_id] = {
                entry for entry in raw_provenance if isinstance(entry, str)
            }
        else:
            provenance_by_source[source_id] = set()

    edge_pairs: set[tuple[str, str]] = set()
    for source_id, body in bodies:
        for match in _LINK_RE.finditer(_mask_fenced_code_blocks(body)):
            target_id = match.group(1).removesuffix(".md")
            if target_id in node_ids:
                edge_pairs.add((source_id, target_id))
    for source_id, target_id in sorted(edge_pairs):
        relation_type = (
            "derived_from"
            if target_id in provenance_by_source.get(source_id, set())
            else None
        )
        conn.execute(_INSERT_EDGE_SQL, (source_id, target_id, relation_type))

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

    candidate_report = CandidateReport()
    if candidates is not None:
        # Dedup against BOTH prior passes, in both directions. The design
        # sketch deduped only against body links; that is not enough. A pair
        # a human already typed via `relations:` would otherwise gain a
        # redundant NULL row -- filtered right back out of suggestions by
        # `edge_typing._candidate_edges`' pair-level exclusion, but still
        # inflating `graph_edge_summary`'s total and firing the "untyped but
        # all excluded" message for a pair nobody needs to revisit. Both
        # directions because links are directed and proximity is not.
        seen: set[tuple[str, str]] = set()
        for source_id, target_id in edge_pairs:
            seen.add((source_id, target_id))
            seen.add((target_id, source_id))
        for source_id, target_id, _ in typed_edges:
            seen.add((source_id, target_id))
            seen.add((target_id, source_id))
        # Source-exclusion (#378 slice 1): a `Source` document must not
        # propose (anchor list) or receive (row guards) a candidate edge.
        # Narrowing the anchor list alone is not enough --
        # `VectorProximitySource.pairs` queries the whole `vectors.db` and
        # never filters its own hits against the ids it was handed, so a
        # Source can still come back as a neighbor even when it was never
        # offered as an anchor. Both endpoints must be checked against the
        # Source-free `seed_node_ids`, not the full `node_ids`.
        seed_node_ids = {
            concept_id
            for concept_id, metadata in metadatas
            if metadata.get("type") != "Source"
        }
        # #378 slice 2: a `dict` keyed by the canonical `(min, max)` pair,
        # retaining the SMALLEST distance per pair -- mirroring
        # `proximity.py`'s own tie rule -- rather than the bare
        # `set[tuple[str, str]]` slice 1 used, which discarded `distance`,
        # the ranking key the cap needs. Endpoint guard, self-pair drop, and
        # `seen` dedup ALL run here, inside this single comprehension, so
        # `best` already holds the fully deduped candidate set BEFORE any
        # ranking or truncation happens -- dedup runs before the cap, never
        # after (`contradiction.py`'s post-review HIGH correction: filtering
        # after a cap lets discarded rows consume cap slots and starve
        # eligible ones).
        # #841: one source's degraded extraction must not become O(N^2)
        # candidate structure. Objects stored WITHOUT judge selection
        # (#772's quarantine tokens on their Source) are near-boilerplate
        # by construction, so their mutual pairs are proximity's cheapest
        # false positives -- each one an LLM call downstream and permanent
        # typed structure once accepted. A pair is withheld exactly when
        # both endpoints CITE a common quarantined source (`provenance:`);
        # a cross pair keeping one healthy endpoint survives, because the
        # gate bounds the degraded cluster, never the healthy neighbor's
        # connectivity. Withheld BEFORE `best` -- the filter-before-cap
        # rule -- so withheld pairs never starve cap slots, and recorded on
        # the report so the drop is never silent. The tokens are spelled as
        # literals, not imported from `okf`, for the same reason
        # `lint._UNJUDGED_NOTICE_CAUSES` gives for its own copies: a typo
        # in either module then fails a test instead of silently agreeing
        # with itself, which a shared constant cannot do. An endpoint with
        # no `provenance:` cannot cite a quarantined source and keeps its
        # pair -- attribution fails open, since withholding on missing
        # data would punish hand-authored documents. The projection is
        # derived state: a re-ingest whose judge answers clears the
        # marker, and the next rebuild reconsiders the pairs.
        quarantined_sources = {
            concept_id
            for concept_id, metadata in metadatas
            if metadata.get("type") == "Source"
            and metadata.get("extraction_notice")
            in ("judge-selection-unavailable", "judge-selection-empty")
        }
        quarantine_dropped: dict[tuple[str, str], set[str]] = {}
        best: dict[tuple[str, str], float] = {}
        for pair in candidates.pairs(sorted(seed_node_ids)):
            if (
                pair.source_id not in seed_node_ids
                or pair.target_id not in seed_node_ids
            ):
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
        # Rank by distance ascending, tie-broken by `(source_id, target_id)`
        # -- matching `pairs()`'s own `sorted(best)` determinism guarantee
        # -- THEN slice to the cap. Select by distance, insert by id: the
        # retained slice is re-sorted by id before insert so the on-disk
        # projection's byte identity (insertion order) matches the
        # pre-slice-2 build for any under-cap bundle.
        ranked = sorted(best, key=lambda pair_key: (best[pair_key], pair_key))
        # #567 paging: the window slides by `candidate_offset` ranked pairs;
        # the default 0 reproduces the pre-#567 `[:cap]` slice exactly. An
        # offset at or past the set retains nothing -- honest emptiness,
        # never a wrap-around.
        retained_keys = ranked[
            candidate_offset : candidate_offset + _MAX_CANDIDATE_EDGES
        ]
        retained = sorted(retained_keys)
        candidate_report = CandidateReport(
            produced=len(best),
            retained=len(retained_keys),
            pairs=tuple(ranked),
            offset=candidate_offset,
            quarantine_withheld=tuple(
                WithheldCandidate(pair=pair, source_ids=tuple(sorted(sources)))
                for pair, sources in sorted(quarantine_dropped.items())
            ),
        )
        for source_id, target_id in retained:
            conn.execute(_INSERT_EDGE_SQL, (source_id, target_id, None))

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
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DROP TABLE IF EXISTS nodes")
            conn.execute("DROP TABLE IF EXISTS edges")
            _populate_graph_tables(conn, bundle_dir, candidates=candidates)
            digest = (
                manifest_hash
                if manifest_hash is not None
                else derived.bundle_manifest_hash(bundle_dir)
            )
            derived.write_manifest_hash(conn, digest)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
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
    uri = f"file:{quote(str(path))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
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

    A thin, graph-specific wrapper around `derived.reindex_gate` (the shared
    manifest-gate-and-rebuild helper, task 2.11 REFACTOR): the gate decides
    skip-vs-rebuild by comparing the bundle's CURRENT manifest hash against
    the PREVIOUSLY stored one (D2 binding contract), then calls
    `write_graph_store` with that SAME digest on a mismatch/absent/`force`.

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

    def write(
        path: Path, bundle_dir: Path, *, manifest_hash: str | None = None
    ) -> None:
        """Parameter NAMES matter: `derived.DerivedStoreWriter` is a callable
        Protocol, so they are part of the contract, not free-form."""
        write_graph_store(
            path, bundle_dir, manifest_hash=manifest_hash, candidates=candidates
        )

    derived.reindex_gate(bundle_dir, path, force=force, write=write)
