"""In-memory FTS5 lexical index over the compiled bundle, plus (Slice 5) an
on-disk persisted variant written only by `reindex`.

The canonical-layer foundation for lexical retrieval (docs/architecture.md:96):
`build_index` opens a rebuild-per-run `sqlite3(":memory:")` connection,
creates one FTS5 virtual table, and populates it from a single
`okf._iter_docs` pass -- one row per non-reserved concept/Source `.md` file,
indexing frontmatter `title`/`description`/`tags` and the markdown body.
`FtsIndex.search` returns `FtsHit`s keyed by the OKF concept ID, ranked by
FTS5's native `bm25`. The in-memory `build_index` path never touches disk and
has no CLI surface of its own -- its only consumer is `retrieval/answer.py`.

Body text is obtained by RE-PARSING each doc via `okf.load_frontmatter`
rather than extending `okf.DocScan` with a body field, keeping `okf.py`
byte-unchanged (design D2) -- `_iter_docs` is reused read-only for
enumeration, reserved-filename skip, and read/parse-error degradation.
That second read/parse is itself guarded (mirrors `lint.collect_docs`'s
TOCTOU-safe re-read): a doc that vanishes or corrupts between `_iter_docs`'s
first pass and this second read is skipped and noted, never crashing the
build. Any exception during the build loop closes the connection before
propagating, so a failed build never leaks it. `_populate_docs_table` is the
shared row-population core both `build_index` (against a fresh `:memory:`
connection) and `write_fts_index` (against an on-disk connection opened via
`state/derived.py`) delegate to -- one doc-walk implementation, two targets.

`write_fts_index(path, bundle_dir)` (Slice 5, `derived-index-cache`) is
invoked ONLY by `reindex`: it always performs a full rebuild (DROP + repopulate)
against `.openkos/fts.db`, targeted via `derived.open_derived_connection`'s
WAL/busy_timeout/lazy-create posture, and stores the bundle's current
manifest hash alongside it. The SKIP-vs-REBUILD decision (comparing that
manifest hash against the stored one) lives entirely in `state/reindex.py` --
this function itself never decides whether to run, only how to write when
called. `open_fts_index_readonly(path)` is the read-only counterpart every
non-`reindex` consumer (`query`/`answer()`) uses: existence-gated (returns
`None` for an absent file, never creating one), opened via a `file:...?mode=ro`
URI connection so a write attempt against the returned handle fails at the
SQLite level, and it NEVER computes or compares a manifest hash -- staleness
detection is exclusively `reindex`'s job (D2 binding contract).
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from urllib.parse import quote

from openkos.model import okf
from openkos.state import derived

_CREATE_TABLE_SQL = """
CREATE VIRTUAL TABLE docs USING fts5(
    concept_id UNINDEXED,
    title, description, tags, body,
    tokenize = 'unicode61'
)
"""

_INSERT_SQL = (
    "INSERT INTO docs (concept_id, title, description, tags, body) "
    "VALUES (?, ?, ?, ?, ?)"
)

_SEARCH_SQL = (
    "SELECT concept_id, rank FROM docs WHERE docs MATCH ? "
    "ORDER BY rank, concept_id LIMIT ?"
)


class FtsUnavailable(RuntimeError):
    """Raised when SQLite's `fts5` module is not compiled in (D7)."""


@dataclass(frozen=True)
class FtsHit:
    """One search result: an OKF concept ID and its `bm25` rank."""

    concept_id: str
    """The OKF concept ID (bundle-relative path, `.md` suffix removed)."""
    score: float
    """The `bm25` rank -- lower is more relevant."""


def _quote_query(query: str) -> str | None:
    """Neutralize `query` into a safe FTS5 `MATCH` expression (D6).

    Each whitespace-separated token is wrapped as a quoted FTS5 string
    (any embedded `"` doubled per FTS5's quoting rule) and the tokens are
    joined with `OR`, so a stray `*`, unbalanced `"`, or bareword operator
    (`AND`/`NEAR`) in the raw query is always treated as literal text, never
    as FTS5 grammar. Returns `None` for an empty/whitespace-only query, so
    the caller can short-circuit without touching SQLite at all.
    """
    tokens = query.split()
    if not tokens:
        return None
    quoted = [f'"{token.replace('"', '""')}"' for token in tokens]
    return " OR ".join(quoted)


class FtsIndex:
    """A rebuild-per-run FTS5 index handle; owns its `sqlite3` connection.

    A context manager (D5): `with build_index(bundle) as idx: idx.search(...)`
    closes the in-memory connection on block exit, which drops the database.
    """

    skipped: list[str]
    """One note per unreadable/unparseable file skipped during the build,
    shaped like `lint.collect_docs`'s skip notices."""

    def __init__(self, conn: sqlite3.Connection, skipped: list[str]) -> None:
        """Wrap an already-populated `conn` and its build-time `skipped` notes."""
        self._conn = conn
        self.skipped = skipped

    def search(self, query: str, limit: int = 10) -> list[FtsHit]:
        """Return up to `limit` `FtsHit`s for `query`, ranked by `bm25` ascending.

        Never raises: an empty/whitespace `query` short-circuits to `[]`
        before touching SQLite, and any `sqlite3.OperationalError` from the
        `MATCH` grammar (defensive, on top of `_quote_query`'s neutralizing)
        also degrades to `[]` rather than propagating (D6).
        """
        match_expr = _quote_query(query)
        if match_expr is None:
            return []
        try:
            rows = self._conn.execute(_SEARCH_SQL, (match_expr, limit)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [FtsHit(concept_id=str(row[0]), score=float(row[1])) for row in rows]

    def close(self) -> None:
        """Close the underlying connection, dropping the in-memory database."""
        self._conn.close()

    def __enter__(self) -> "FtsIndex":
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


def _skip_note(concept_id: str, *, reason: str) -> str:
    """Build one skip notice, `lint.collect_docs`-shaped."""
    return f"{concept_id}.md: skipped ({reason})"


def _populate_docs_table(conn: sqlite3.Connection, bundle_dir: Path) -> list[str]:
    """Shared row-population core (D-refactor, dedupes the in-memory/on-disk
    writer paths): creates the `docs` FTS5 virtual table on `conn` (raising
    `FtsUnavailable` if `fts5` is not compiled into SQLite, D7), then walks
    `okf._iter_docs(bundle_dir)` once, inserting one row per eligible
    document and returning the skip notices for anything that could not be
    indexed.

    A `read_error`/`parse_error` doc is skipped and noted, never crashing the
    build (mirrors `lint`/`survey_bundle`); a valid doc has its body
    re-parsed via `okf.load_frontmatter` (D2) and is inserted as one row
    keyed by its OKF concept ID (bundle-relative path, `.md` suffix removed)
    -- the same identity `forget` uses. That second read/parse is itself
    guarded (`lint.collect_docs`-shaped): a doc that vanishes or becomes
    unparseable between the two passes (e.g. a concurrent `forget`) is
    skipped and noted too, instead of crashing the build. Callers own
    `conn`'s lifecycle -- any exception raised here propagates to the caller
    unchanged, closing/cleanup is the caller's responsibility.
    """
    try:
        conn.execute(_CREATE_TABLE_SQL)
    except sqlite3.OperationalError as exc:
        raise FtsUnavailable(
            "SQLite's fts5 module is not available in this environment"
        ) from exc

    skipped: list[str] = []
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
        title = str(metadata.get("title") or "")
        description = str(metadata.get("description") or "")
        tags = metadata.get("tags")
        tags_text = " ".join(str(tag) for tag in tags) if isinstance(tags, list) else ""
        conn.execute(_INSERT_SQL, (concept_id, title, description, tags_text, body))
    return skipped


def build_index(bundle_dir: Path) -> FtsIndex:
    """Build an in-memory FTS5 index over every eligible doc under `bundle_dir`.

    Opens `sqlite3(":memory:")` and delegates to `_populate_docs_table` for
    the table DDL + doc-walk + insert sequence. Any exception raised
    anywhere in that call (including a `FtsUnavailable` DDL failure, the
    guarded per-doc skip cases re-raising, or a lower-level SQLite failure)
    closes the in-memory connection before propagating -- a failed build
    never leaks it; only a successful build hands the open connection off
    to the returned `FtsIndex`, which owns it thereafter. Never touches disk
    -- persistence exists only via the distinct `write_fts_index` path
    `reindex` calls (fts-state: Index never touches disk).
    """
    conn = sqlite3.connect(":memory:")
    try:
        skipped = _populate_docs_table(conn, bundle_dir)
    except BaseException:
        conn.close()
        raise

    return FtsIndex(conn, skipped)


def write_fts_index(
    path: Path, bundle_dir: Path, *, manifest_hash: str | None = None
) -> None:
    """Write a full, on-disk FTS5 index for `bundle_dir` to `path`, invoked
    ONLY by `reindex` (derived-index-cache: On-Disk Persistence Of Derived
    Indexes; fts-state: Reindex persists the FTS index to disk).

    Opens `path` via `state.derived.open_derived_connection` (lazy
    `.openkos/` creation, WAL + busy_timeout PRAGMAs, shared `meta` table),
    then drops any prior `docs` table and delegates to the SAME
    `_populate_docs_table` core `build_index` uses -- the on-disk index
    always contains exactly the rows an equivalent `build_index` call would
    produce in memory. This function always performs a full rebuild when
    called -- it makes no skip/rebuild decision of its own; that comparison
    against the PREVIOUSLY stored manifest hash is `state/reindex.py`'s
    exclusive responsibility (D2 binding contract).

    `manifest_hash`, when given, is stored VERBATIM rather than recomputed
    here: `state/reindex.py`'s `_reindex_fts` passes the SAME digest it
    already computed for its skip/rebuild decision, so the stored value
    always corresponds to that exact bundle snapshot instead of a THIRD,
    independently-taken walk that could observe a bundle mutation the
    decision/populate passes did not (review correction, Finding C --
    triple-walk/TOCTOU). Omitting it (the default) computes one fresh, for
    direct/standalone callers with no separate decision step of their own.

    The `DROP`, the full rebuild, and the manifest write ALL happen inside
    one explicit SQLite transaction (`BEGIN IMMEDIATE` ... `commit()`/
    `rollback()`) rather than relying on `sqlite3`'s own implicit-transaction
    handling: left to itself, a DDL statement (`DROP TABLE`, `CREATE VIRTUAL
    TABLE`) auto-commits independently of the later `INSERT`/meta-write
    commit, so a crash mid-rebuild could destroy the PRIOR working index
    without ever completing the new one (review correction, Finding B).
    Wrapping the whole sequence in one explicit transaction makes the
    rebuild atomic: any failure here rolls back completely, leaving the
    PRIOR `docs` table and PRIOR `meta.manifest_hash` exactly as they were --
    the next `reindex` run's manifest comparison then correctly sees the
    same (stale) hash and retries the rebuild, rather than silently reading
    an empty index forever.
    """
    conn = derived.open_derived_connection(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DROP TABLE IF EXISTS docs")
            _populate_docs_table(conn, bundle_dir)
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


def open_fts_index_readonly(path: Path) -> "FtsIndex | None":
    """Open the on-disk FTS index at `path` read-only, for `query`/`answer()`
    (derived-index-cache: Consumers Read Persisted Indexes Read-Only;
    fts-state: Persisted index read-only for non-reindex consumers).

    Existence-gated: returns `None` if `path` does not exist rather than
    creating one -- only `reindex`'s `write_fts_index` ever creates this
    file. Opens via a `file:...?mode=ro` SQLite URI connection, so the
    returned handle's connection genuinely refuses any write attempt at the
    SQLite level (not merely by convention). NEVER computes or compares a
    bundle manifest hash -- staleness detection is exclusively `reindex`'s
    job; a caller here always gets whatever `reindex` last wrote, however
    stale, exactly like the shipped `vectors.db` read path already behaves.
    """
    if not path.exists():
        return None
    uri = f"file:{quote(str(path))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    return FtsIndex(conn, [])
