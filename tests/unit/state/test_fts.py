"""Unit tests for `state/fts.py`: the in-memory FTS5 lexical index.

`state/fts.py` is the canonical-layer foundation for lexical retrieval:
`build_index` walks a bundle via `okf._iter_docs` (single pass, reserved-skip,
graceful degradation) and populates a rebuild-per-run, in-memory FTS5 table;
`FtsIndex.search` returns OKF concept IDs ranked by `bm25` relevance. The
module never touches disk and exposes no CLI surface -- its only consumer is
the future `query` command.
"""

import ast
import dataclasses
import sqlite3
from pathlib import Path

import pytest

from openkos.model import okf
from openkos.state import fts

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _write_doc(
    path: Path,
    *,
    doc_type: str = "Concept",
    title: str = "Stub",
    description: str = "",
    tags: list[str] | None = None,
    body: str = "",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tags_line = f"tags: [{', '.join(tags)}]\n" if tags is not None else ""
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: {title}\ndescription: {description}\n"
        f"{tags_line}---\n{body}",
        encoding="utf-8",
    )


# --- Phase 1: scaffold -------------------------------------------------


def test_fts_hit_is_a_frozen_dataclass() -> None:
    """`FtsHit` carries `concept_id` and `score`, and is immutable."""
    hit = fts.FtsHit(concept_id="concepts/stoicism", score=-1.5)

    assert hit.concept_id == "concepts/stoicism"
    assert hit.score == -1.5
    with pytest.raises(dataclasses.FrozenInstanceError):
        hit.score = 0.0  # type: ignore[misc]


def test_fts_unavailable_is_a_runtime_error() -> None:
    """`FtsUnavailable` subclasses `RuntimeError`, per D7."""
    assert issubclass(fts.FtsUnavailable, RuntimeError)


# --- Phase 2: build -- enumeration, identity, reserved-skip, empty bundle


def test_build_index_creates_one_row_per_eligible_document(tmp_path: Path) -> None:
    """Every non-reserved concept/Source doc becomes exactly one row."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz",
    )
    _write_doc(
        bundle_dir / "sources" / "call.md",
        doc_type="Source",
        title="Call",
        body="phonezz",
    )

    with fts.build_index(bundle_dir) as idx:
        stoicism_hits = idx.search("dichotomyzz")
        call_hits = idx.search("phonezz")

    assert [h.concept_id for h in stoicism_hits] == ["concepts/stoicism"]
    assert [h.concept_id for h in call_hits] == ["sources/call"]


def test_build_index_identity_is_bundle_relative_path_minus_md(
    tmp_path: Path,
) -> None:
    """A hit's `concept_id` is the bundle-relative path with `.md` removed."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="Unique term zzyzx.",
    )

    with fts.build_index(bundle_dir) as idx:
        hits = idx.search("zzyzx")

    assert len(hits) == 1
    assert hits[0].concept_id == "concepts/stoicism"


def test_build_index_reserved_filenames_never_indexed(tmp_path: Path) -> None:
    """`index.md`/`log.md` never appear as rows in the resulting index."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "index.md").write_text("# zzyzxindex\n", encoding="utf-8")
    (bundle_dir / "log.md").write_text("# zzyzxlog\n", encoding="utf-8")

    with fts.build_index(bundle_dir) as idx:
        assert idx.search("zzyzxindex") == []
        assert idx.search("zzyzxlog") == []


def test_build_index_empty_bundle_produces_empty_index(tmp_path: Path) -> None:
    """An empty bundle builds successfully with zero rows; `search` never raises."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    with fts.build_index(bundle_dir) as idx:
        assert idx.search("anything", limit=10) == []


# --- Phase 3: content fields --------------------------------------------


def test_build_index_body_term_is_searchable(tmp_path: Path) -> None:
    """A distinctive body term resolves a `search()` hit to its concept ID."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="A distinctive term: dichotomycontrolzz.",
    )

    with fts.build_index(bundle_dir) as idx:
        hits = idx.search("dichotomycontrolzz")

    assert [h.concept_id for h in hits] == ["concepts/stoicism"]


def test_build_index_tag_term_is_searchable(tmp_path: Path) -> None:
    """A `tags: [philosophy]` document is hit by `search("philosophy")`."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", tags=["philosophy"])

    with fts.build_index(bundle_dir) as idx:
        hits = idx.search("philosophy")

    assert [h.concept_id for h in hits] == ["concepts/stoicism"]


def test_build_index_missing_tags_does_not_crash(tmp_path: Path) -> None:
    """A document with no `tags` key indexes without crashing."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")

    with fts.build_index(bundle_dir) as idx:
        hits = idx.search("Stoicism")

    assert [h.concept_id for h in hits] == ["concepts/stoicism"]


def test_build_index_non_list_tags_does_not_crash(tmp_path: Path) -> None:
    """A non-list `tags` value degrades to an empty tags column, never crashes."""
    bundle_dir = tmp_path / "bundle"
    doc = bundle_dir / "concepts" / "stoicism.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(
        "---\ntype: Concept\ntitle: Stoicism\ntags: not-a-list\n---\nBody.\n",
        encoding="utf-8",
    )

    with fts.build_index(bundle_dir) as idx:
        hits = idx.search("Stoicism")

    assert [h.concept_id for h in hits] == ["concepts/stoicism"]


# --- Phase 4: degradation -------------------------------------------------


def test_build_index_skips_unreadable_file(tmp_path: Path) -> None:
    """An unreadable file is skipped and noted; valid docs still index."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "readable.md", title="Readable")
    unreadable = bundle_dir / "concepts" / "unreadable.md"
    unreadable.write_bytes(b"\xff\xfe\x00\x01not-utf8")

    with fts.build_index(bundle_dir) as idx:
        hits = idx.search("Readable")
        assert [h.concept_id for h in hits] == ["concepts/readable"]
        assert idx.skipped == ["concepts/unreadable.md: skipped (unreadable)"]


def test_build_index_skips_unparseable_frontmatter(tmp_path: Path) -> None:
    """A file with no parseable frontmatter is skipped and noted."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "readable.md", title="Readable")
    (bundle_dir / "concepts" / "broken.md").write_text(
        "Just plain text, no frontmatter block.\n", encoding="utf-8"
    )

    with fts.build_index(bundle_dir) as idx:
        hits = idx.search("Readable")
        assert [h.concept_id for h in hits] == ["concepts/readable"]
        assert idx.skipped == ["concepts/broken.md: skipped (unparseable frontmatter)"]


def test_build_index_skips_doc_whose_second_read_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A doc that vanishes between `_iter_docs`'s first read and `build_index`'s
    second body re-read (e.g. a concurrent `openkos forget`) is skipped and
    noted, never crashing the whole build -- mirrors `lint.collect_docs`'s
    TOCTOU-safe re-read guard."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stable.md", title="Stable", body="stablezzy")
    flaky_path = bundle_dir / "concepts" / "flaky.md"
    _write_doc(flaky_path, title="Flaky", body="flakyzzy")

    read_counts: dict[Path, int] = {}
    original_read_text = Path.read_text

    def flaky_read_text(
        self: Path,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> str:
        if self == flaky_path:
            read_counts[self] = read_counts.get(self, 0) + 1
            if read_counts[self] > 1:
                raise FileNotFoundError("simulated concurrent delete between reads")
        return original_read_text(
            self, encoding=encoding, errors=errors, newline=newline
        )

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    with fts.build_index(bundle_dir) as idx:
        stable_hits = idx.search("stablezzy")
        flaky_hits = idx.search("flakyzzy")

    assert [h.concept_id for h in stable_hits] == ["concepts/stable"]
    assert flaky_hits == []
    assert idx.skipped == ["concepts/flaky.md: skipped (unreadable)"]


def test_build_index_skips_doc_whose_second_parse_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A doc whose frontmatter becomes unparseable between `_iter_docs`'s
    first parse and `build_index`'s second `okf.load_frontmatter` re-parse is
    skipped and noted, never crashing the whole build."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stable.md", title="Stable", body="stablezzy")
    _write_doc(bundle_dir / "concepts" / "flaky.md", title="Flaky", body="flakyzzy")

    original_load_frontmatter = okf.load_frontmatter

    def flaky_load_frontmatter(text: str) -> tuple[dict[str, object], str]:
        if "flakyzzy" in text:
            raise ValueError("simulated corrupted frontmatter on re-read")
        return original_load_frontmatter(text)

    monkeypatch.setattr(okf, "load_frontmatter", flaky_load_frontmatter)

    with fts.build_index(bundle_dir) as idx:
        stable_hits = idx.search("stablezzy")
        flaky_hits = idx.search("flakyzzy")

    assert [h.concept_id for h in stable_hits] == ["concepts/stable"]
    assert flaky_hits == []
    assert idx.skipped == ["concepts/flaky.md: skipped (unparseable frontmatter)"]


# --- Phase 5: search -- ranking, limit, safety ----------------------------


def test_search_orders_hits_by_bm25_rank_ascending(tmp_path: Path) -> None:
    """A document matching the term more strongly ranks first (lower score)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "heavy.md",
        title="Zynthaxis",
        body="zynthaxis zynthaxis zynthaxis zynthaxis",
    )
    _write_doc(
        bundle_dir / "concepts" / "light.md",
        title="Other",
        body="zynthaxis appears once here",
    )

    with fts.build_index(bundle_dir) as idx:
        hits = idx.search("zynthaxis")

    assert len(hits) == 2
    assert hits[0].concept_id == "concepts/heavy"
    assert hits[0].score <= hits[1].score


def test_search_limit_caps_results_with_deterministic_tie_break(
    tmp_path: Path,
) -> None:
    """`limit` bounds the number of hits; a tied `bm25` rank across
    identical documents breaks deterministically by `concept_id` ascending,
    so which docs survive truncation never depends on unspecified SQLite
    tie-break behavior."""
    bundle_dir = tmp_path / "bundle"
    for i in range(5):
        _write_doc(
            bundle_dir / "concepts" / f"doc{i}.md",
            title="Common",
            body="common term shared",
        )

    with fts.build_index(bundle_dir) as idx:
        hits = idx.search("common", limit=2)

    assert [h.concept_id for h in hits] == ["concepts/doc0", "concepts/doc1"]


def test_search_breaks_bm25_ties_by_concept_id_regardless_of_insertion_order() -> None:
    """Even when rows are inserted in the OPPOSITE of `concept_id` order, a
    tied `bm25` rank still resolves ascending by `concept_id` -- proving the
    tie-break is a genuine SQL `ORDER BY` secondary key, not an accident of
    `_iter_docs`'s already-sorted insertion order (`build_index` always
    inserts in sorted-path order, so this bypasses it via the same table/
    insert SQL constants to isolate `_SEARCH_SQL` itself)."""
    conn = sqlite3.connect(":memory:")
    conn.execute(fts._CREATE_TABLE_SQL)
    for concept_id in ("concepts/zebra", "concepts/mango", "concepts/apple"):
        conn.execute(
            fts._INSERT_SQL,
            (concept_id, "Common", "", "", "common term shared"),
        )
    idx = fts.FtsIndex(conn, [])

    hits = idx.search("common", limit=2)
    idx.close()

    assert [h.concept_id for h in hits] == ["concepts/apple", "concepts/mango"]


def test_search_no_match_returns_empty_list(tmp_path: Path) -> None:
    """A query term absent from every document returns `[]`, never an error."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")

    with fts.build_index(bundle_dir) as idx:
        assert idx.search("nonexistenttermzzz") == []


@pytest.mark.parametrize(
    "query",
    [
        "*",
        '"unbalanced',
        "AND",
        "NEAR",
        "term1 AND term2",
        "term1 * term2",
        '"quoted phrase"',
    ],
)
def test_search_never_raises_on_fts5_syntax(tmp_path: Path, query: str) -> None:
    """A raw FTS5-grammar query never raises `OperationalError` (D6)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")

    with fts.build_index(bundle_dir) as idx:
        hits = idx.search(query)

    assert isinstance(hits, list)


class _FailingConn:
    """Stub connection whose `execute` fails loudly if it is ever called."""

    def execute(self, *args: object, **kwargs: object) -> None:
        """Raise, proving the caller never reached SQLite for this query."""
        raise AssertionError("should not query sqlite for an empty query")

    def close(self) -> None:
        """No-op, so `FtsIndex.__exit__`'s `close()` call is harmless."""


class _RaisingOperationalErrorConn:
    """Stub connection whose `execute` always raises `OperationalError`.

    Exercises `search`'s defensive `except sqlite3.OperationalError` branch
    directly -- `_quote_query`'s per-token quoting already neutralizes every
    FTS5-grammar case tried against a real connection, so this simulates the
    residual defensive path without needing a query string that defeats it.
    """

    def execute(self, *args: object, **kwargs: object) -> None:
        """Raise, simulating a `MATCH` grammar failure `_quote_query` missed."""
        raise sqlite3.OperationalError("simulated MATCH grammar failure")

    def close(self) -> None:
        """No-op, so `FtsIndex.__exit__`'s `close()` call is harmless."""


def test_search_returns_empty_on_operational_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `sqlite3.OperationalError` from `MATCH` degrades to `[]`, never raises."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")

    with fts.build_index(bundle_dir) as idx:
        real_conn = idx._conn
        monkeypatch.setattr(idx, "_conn", _RaisingOperationalErrorConn())

        assert idx.search("Stoicism") == []

        real_conn.close()


def test_search_empty_or_whitespace_query_short_circuits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty/whitespace query returns `[]` without touching SQLite."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")

    with fts.build_index(bundle_dir) as idx:
        real_conn = idx._conn
        monkeypatch.setattr(idx, "_conn", _FailingConn())

        assert idx.search("") == []
        assert idx.search("   ") == []

        real_conn.close()


# --- Phase 6: lifecycle ----------------------------------------------------


def test_context_manager_closes_connection_after_block(tmp_path: Path) -> None:
    """`with build_index(...) as idx:` closes the connection on block exit."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    with fts.build_index(bundle_dir) as idx:
        assert idx.search("anything") == []
        conn = idx._conn

    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_close_can_be_called_directly(tmp_path: Path) -> None:
    """`close()` drops the in-memory database outside a `with` block too."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    idx = fts.build_index(bundle_dir)

    idx.close()

    with pytest.raises(sqlite3.ProgrammingError):
        idx._conn.execute("SELECT 1")


class _NoFts5Connection(sqlite3.Connection):
    """A `sqlite3.Connection` subclass simulating a missing `fts5` module.

    `sqlite3.Connection` is a C-extension type: its `execute` cannot be
    monkeypatched on the class or on an instance directly, so this
    subclasses it instead -- the only way to override behavior for one
    connection while leaving the real `sqlite3.Connection` untouched.
    """

    def execute(self, sql: str, *args: object, **kwargs: object) -> sqlite3.Cursor:
        """Raise `OperationalError` for the FTS5 DDL, delegate everything else."""
        if "VIRTUAL TABLE" in sql:
            raise sqlite3.OperationalError("no such module: fts5")
        return super().execute(sql, *args, **kwargs)  # type: ignore[arg-type]


def test_build_index_raises_fts_unavailable_when_fts5_not_compiled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `CREATE VIRTUAL TABLE ... fts5` failure raises `FtsUnavailable` (D7)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    original_connect = sqlite3.connect

    def fake_connect(
        database: str, *args: object, **kwargs: object
    ) -> sqlite3.Connection:
        return original_connect(database, factory=_NoFts5Connection)

    monkeypatch.setattr(sqlite3, "connect", fake_connect)

    with pytest.raises(fts.FtsUnavailable):
        fts.build_index(bundle_dir)


class _FailingInsertConnection(sqlite3.Connection):
    """A `sqlite3.Connection` subclass that fails on `INSERT`, succeeds
    otherwise -- simulates a mid-build failure after the table DDL succeeds,
    to prove the connection is released rather than leaked (subclassed for
    the same C-extension reason as `_NoFts5Connection` above)."""

    def execute(self, sql: str, *args: object, **kwargs: object) -> sqlite3.Cursor:
        """Raise `OperationalError` for `INSERT`, delegate everything else."""
        if sql.strip().startswith("INSERT"):
            raise sqlite3.OperationalError("simulated mid-build insert failure")
        return super().execute(sql, *args, **kwargs)  # type: ignore[arg-type]


def test_build_index_closes_connection_on_mid_build_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception raised mid-build (after the table exists, while
    inserting a row) still closes the in-memory connection -- it must not
    leak, waiting only on GC, when `build_index` never returns an `FtsIndex`
    to own it."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    original_connect = sqlite3.connect
    captured: dict[str, sqlite3.Connection] = {}

    def fake_connect(
        database: str, *args: object, **kwargs: object
    ) -> sqlite3.Connection:
        conn = original_connect(database, factory=_FailingInsertConnection)
        captured["conn"] = conn
        return conn

    monkeypatch.setattr(sqlite3, "connect", fake_connect)

    with pytest.raises(sqlite3.OperationalError):
        fts.build_index(bundle_dir)

    with pytest.raises(sqlite3.ProgrammingError):
        captured["conn"].execute("SELECT 1")


# --- Phase 7: no-disk + no-CLI guards --------------------------------------


def test_build_index_and_search_never_write_to_disk(tmp_path: Path) -> None:
    """No `.openkos/`, `openkos.db`, or `.gitignore` entry appears anywhere."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    before = set(tmp_path.rglob("*"))

    with fts.build_index(bundle_dir) as idx:
        idx.search("Stoicism")

    after = set(tmp_path.rglob("*"))
    assert after == before
    assert not (bundle_dir / ".openkos").exists()
    assert not (tmp_path / "openkos.db").exists()
    assert not (bundle_dir / ".gitignore").exists()


def test_cli_module_does_not_import_state_fts() -> None:
    """`state/fts.py` is imported by no CLI module (No CLI Surface scenario)."""
    cli_main = _REPO_ROOT / "src" / "openkos" / "cli" / "main.py"
    tree = ast.parse(cli_main.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert not any("state" in name for name in imported)


# --- Phase 8: integration fixture -------------------------------------------


def test_build_index_over_good_life_demo_bundle_resolves_expected_concepts() -> None:
    """Building over the demo bundle resolves stoicism/apatheia/philosophy hits."""
    bundle_dir = _REPO_ROOT / "examples" / "good-life-demo" / "bundle"

    with fts.build_index(bundle_dir) as idx:
        stoicism_hits = idx.search("stoicism")
        apatheia_hits = idx.search("apatheia")
        philosophy_hits = idx.search("philosophy")

    assert "concepts/stoicism" in [h.concept_id for h in stoicism_hits]
    assert "concepts/stoicism" in [h.concept_id for h in apatheia_hits]
    assert "concepts/stoicism" in [h.concept_id for h in philosophy_hits]
