"""In-memory FTS5 lexical index over the compiled bundle.

The canonical-layer foundation for lexical retrieval (docs/architecture.md:96):
`build_index` opens a rebuild-per-run `sqlite3(":memory:")` connection,
creates one FTS5 virtual table, and populates it from a single
`okf._iter_docs` pass -- one row per non-reserved concept/Source `.md` file,
indexing frontmatter `title`/`description`/`tags` and the markdown body.
`FtsIndex.search` returns `FtsHit`s keyed by the OKF concept ID, ranked by
FTS5's native `bm25`. The index never touches disk and has no CLI surface;
its only consumer is the future `query` command.

Body text is obtained by RE-PARSING each doc via `okf.load_frontmatter`
rather than extending `okf.DocScan` with a body field, keeping `okf.py`
byte-unchanged (design D2) -- `_iter_docs` is reused read-only for
enumeration, reserved-filename skip, and read/parse-error degradation.
That second read/parse is itself guarded (mirrors `lint.collect_docs`'s
TOCTOU-safe re-read): a doc that vanishes or corrupts between `_iter_docs`'s
first pass and this second read is skipped and noted, never crashing the
build. Any exception during the build loop closes the in-memory connection
before propagating, so a failed build never leaks it.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from openkos.model import okf

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


def build_index(bundle_dir: Path) -> FtsIndex:
    """Build an in-memory FTS5 index over every eligible doc under `bundle_dir`.

    Opens `sqlite3(":memory:")`, creates the FTS5 virtual table (raising
    `FtsUnavailable` if `fts5` is not compiled into SQLite, D7), then walks
    `okf._iter_docs` once: a `read_error`/`parse_error` doc is skipped and
    noted, never crashing the build (mirrors `lint`/`survey_bundle`); a
    valid doc has its body re-parsed via `okf.load_frontmatter` (D2) and is
    inserted as one row keyed by its OKF concept ID (bundle-relative path,
    `.md` suffix removed) -- the same identity `forget` uses. That second
    read/parse is itself guarded (`lint.collect_docs`-shaped): a doc that
    vanishes or becomes unparseable between the two passes (e.g. a
    concurrent `forget`) is skipped and noted too, instead of crashing the
    build. Any exception raised anywhere in this build (including the
    guarded cases re-raising as a skip, or a lower-level SQLite failure)
    closes the in-memory connection before propagating -- a failed build
    never leaks it; only a successful build hands the open connection off
    to the returned `FtsIndex`, which owns it thereafter.
    """
    conn = sqlite3.connect(":memory:")
    try:
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
            tags_text = (
                " ".join(str(tag) for tag in tags) if isinstance(tags, list) else ""
            )
            conn.execute(_INSERT_SQL, (concept_id, title, description, tags_text, body))
    except BaseException:
        conn.close()
        raise

    return FtsIndex(conn, skipped)
